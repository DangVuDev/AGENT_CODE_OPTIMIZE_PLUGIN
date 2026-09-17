from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application import (
    NodePorts,
    build_s03_runtime,
    build_s04_registrations,
    build_s04_runtime,
)
from production_optimizer.application.node_runtime import NodeRuntime
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.a2 import (
    BaselineSnapshot,
    MetricAggregate,
    RepositoryCommand,
    RepositoryManifest,
    SourceSnapshot,
)
from production_optimizer.contracts.a3 import (
    CriterionImpact,
    ExperimentPhaseTemplate,
    ImpactAssessment,
    RiskAssessment,
    RollbackPlan,
    ScopeResolutionEntry,
    ScopeResolutionReport,
    SolutionPortfolio,
    SolutionStrategy,
    TradeoffAnalysis,
    Treatment,
    ValidationPlan,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import (
    IntentRecord,
    IntentStatus,
    PolicyDecision,
    PolicyRequest,
)
from production_optimizer.contracts.s01 import SelectedSolution, SelectionApproval
from production_optimizer.contracts.s02 import (
    ExecutionPhase,
    ExecutionPlan,
    PlanTask,
    PlanTreatment,
    TaskList,
)
from production_optimizer.contracts.s04 import VerificationReport
from production_optimizer.orchestration.catalog import S03_NODE_IDS, S04_NODE_IDS

_TENANT = "TENANT-S04"
_CASE_ID = "OPT-S04-1"
_ZERO_DIGEST = f"sha256:{'0' * 64}"


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self._content_by_uri: dict[str, bytes] = {}

    def put_json(
        self, *, tenant_id: str, content: bytes, content_digest: str, idempotency_key: str
    ) -> ArtifactRef:
        del tenant_id
        uri = f"memory://{idempotency_key}"
        self._content_by_uri[uri] = content
        return ArtifactRef(
            artifact_type="JsonArtifact", schema_version="1.0", artifact_id=idempotency_key,
            content_digest=content_digest, uri=uri,
        )

    def put_blob(
        self, *, tenant_id: str, content: bytes, content_digest: str, media_type: str
    ) -> ArtifactRef:
        del tenant_id, media_type
        uri = f"memory://blob/{content_digest}"
        self._content_by_uri[uri] = content
        return ArtifactRef(
            artifact_type="Blob", schema_version="1.0", artifact_id=content_digest,
            content_digest=content_digest, uri=uri,
        )

    def read(self, *, tenant_id: str, ref: ArtifactRef) -> bytes:
        del tenant_id
        return self._content_by_uri[ref.uri]

    def verify(self, *, tenant_id: str, ref: ArtifactRef) -> bool:
        del tenant_id
        content = self._content_by_uri.get(ref.uri)
        return content is not None and sha256_digest(content) == ref.content_digest

    def seed_json(self, ref: ArtifactRef, content: bytes) -> None:
        self._content_by_uri[ref.uri] = content


class _MemoryIntentLedger:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IntentRecord] = {}

    def prepare(self, intent: IntentRecord) -> IntentRecord:
        key = (intent.tenant_id, intent.idempotency_key)
        existing = self._records.get(key)
        if existing is not None and existing.status is IntentStatus.COMPLETED:
            return existing
        self._records[key] = intent
        return intent

    def get(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord | None:
        return self._records.get((tenant_id, idempotency_key))

    def complete(
        self, *, tenant_id: str, idempotency_key: str, output_ref: ArtifactRef
    ) -> IntentRecord:
        record = self._records[(tenant_id, idempotency_key)]
        updated = record.model_copy(
            update={"status": IntentStatus.COMPLETED, "output_ref": output_ref}
        )
        self._records[(tenant_id, idempotency_key)] = updated
        return updated

    def mark_unknown(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord:
        record = self._records[(tenant_id, idempotency_key)]
        updated = record.model_copy(update={"status": IntentStatus.UNKNOWN})
        self._records[(tenant_id, idempotency_key)] = updated
        return updated


class _AllowPolicy:
    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=True, decision="allow", policy_version=request.policy_version, reasons=[]
        )

    def healthcheck(self) -> bool:
        return True


def _ports(store: _MemoryArtifactStore) -> NodePorts:
    return NodePorts(artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy())


def _advance(runtime: NodeRuntime, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    merged = dict(state)
    merged.update(result)
    merged["artifact_refs"] = [*existing_refs, *new_refs]
    merged["node_routes"] = {**state.get("node_routes", {}), **result.get("node_routes", {})}
    # `s03_revision_attempts` is a real summing (`operator.add`) reducer in
    # `contracts/state.py` -- LangGraph itself sums contributions across
    # supersteps, but this hand-rolled test harness otherwise does a plain
    # `dict.update`, which would silently overwrite instead of accumulate.
    if "s03_revision_attempts" in result:
        merged["s03_revision_attempts"] = (
            state.get("s03_revision_attempts", 0) + result["s03_revision_attempts"]
        )
    return merged


def _model_from_ref[T: BaseModel](
    store: _MemoryArtifactStore, ref: ArtifactRef, model: type[T]
) -> T:
    content = store.read(tenant_id=_TENANT, ref=ref)
    data = TypeAdapter(dict[str, Any]).validate_json(content)
    data.setdefault("content_digest", ref.content_digest)
    return model.model_validate(data)


def _ref_by_type(state: dict[str, Any], artifact_type: str) -> ArtifactRef:
    for ref in cast("list[ArtifactRef]", state["artifact_refs"]):
        if ref.artifact_type == artifact_type:
            return ref
    raise AssertionError(f"no artifact ref of type {artifact_type!r} in state")


def _base_envelope_kwargs(artifact_type: str) -> dict[str, Any]:
    return {
        "artifact_id": f"{_CASE_ID}-{artifact_type}",
        "tenant_id": _TENANT,
        "case_id": _CASE_ID,
        "created_at": datetime.now(UTC),
        "producer": ProducerIdentity(name="test", version="1.0"),
        "content_digest": _ZERO_DIGEST,
        "parent_digests": [],
    }


def _seal_and_store(store: _MemoryArtifactStore, model: BaseModel) -> ArtifactRef:
    sealed = model.model_copy(update={"content_digest": model_content_digest(model)})
    artifact_type = cast("Any", sealed).artifact_type
    artifact_id = cast("Any", sealed).artifact_id
    digest = cast("Any", sealed).content_digest
    ref = ArtifactRef(
        artifact_type=artifact_type, schema_version="1.0", artifact_id=artifact_id,
        content_digest=digest, uri=f"memory://{artifact_id}",
    )
    store.seed_json(ref, canonical_json(sealed.model_dump(mode="json")))
    return ref


def _seed_repo(tmp_path: Path, *, initial_expr: str = "1 + 1") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(f"def compute():\n    return {initial_expr}\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "from app import compute\n\n\ndef test_compute():\n    assert compute() == 4\n"
    )
    return repo


def _strategy(*, before: str, after: str) -> SolutionStrategy:
    return SolutionStrategy(
        strategy_id="strategy-good",
        finding_ids=["finding-1"],
        title="Fix compute",
        mechanism="Correct the arithmetic in compute()",
        strategy_tradeoffs="None",
        phase_templates=[
            ExperimentPhaseTemplate(
                phase_id="phase-1", sequence=1, phase_kind="implementation",
                treatment=Treatment(variable="compute_body", before=before, after=after),
            )
        ],
        risk_ceiling=cast("Any", "experiment_config"),
        risk_assessment=RiskAssessment(
            assessment_id="risk-1", strategy_id="strategy-good",
            risk_tier=cast("Any", "experiment_config"),
            blast_radius="single feature", reversibility="fast", uncertainty=0.1,
            migration_impact=False, security_impact=False,
        ),
        impact_assessment=ImpactAssessment(
            assessment_id="impact-1", strategy_id="strategy-good",
            criterion_impacts=[
                CriterionImpact(
                    criterion_id="correctness", direction="improves", confidence=0.9,
                    basis="forecast",
                )
            ],
        ),
        tradeoff_analysis=TradeoffAnalysis(
            analysis_id="tradeoff-1", strategy_id="strategy-good", pros=["fixes the bug"],
            cons=["small chance of unrelated regressions"], effort="low", uncertainty=0.1,
        ),
        validation_plan=ValidationPlan(
            plan_id="validation-1", strategy_id="strategy-good",
            benchmark_protocol="rerun the suite", stop_conditions=["still failing"],
        ),
        rollback_plan=RollbackPlan(
            plan_id="rollback-1", strategy_id="strategy-good", mechanism="revert the commit",
            verification="rerun the suite", reversible=True,
        ),
        scope_resolution=ScopeResolutionReport(
            report_id="scope-1", strategy_id="strategy-good",
            entries=[ScopeResolutionEntry(path_or_symbol="app.py", kind="file", exists=True)],
            fully_resolved=True,
        ),
        evidence_ids=["evidence-1"],
        eligible=True,
    )


def _seed_case(
    store: _MemoryArtifactStore, *, repo: Path, before: str, after: str,
    baseline_unit_failing: bool, baseline_lint_failing: bool = False,
    declares_pytest_cov: bool = False, interpreter: str = "python",
) -> list[ArtifactRef]:
    fingerprint = sha256_digest(canonical_json({"case_id": _CASE_ID, "origin": "manual"}))
    request = OptimizationRequest(
        **_base_envelope_kwargs("OptimizationRequest"),
        origin=Origin.MANUAL, scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="app-repo", allowed_root_id="workspace", relative_path=".",
            requested_revision="HEAD",
        ),
        objective=Objective(statement="Fix compute()", feature_id="app"),
        criteria=[
            Criterion(
                criterion_id="correctness", metric_id="unit_command_result",
                direction="minimize", target=0.0, unit="exit_code", weight=1.0,
            )
        ],
        workload=WorkloadContract(
            workload_id="app-workload", environment_id="staging", repetitions=1, warmup_runs=0,
            concurrency=1, cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="unit-samples", criterion_id="correctness",
                accepted_source_types={"benchmark"}, minimum_samples=1,
            )
        ],
        budget=ExecutionBudget(
            deadline_seconds=300, maximum_worker_seconds=120, maximum_model_tokens=2048,
            maximum_storage_bytes=10_000_000,
        ),
        approval=ApprovalBinding(
            approval_id="approval-1", actor_id="owner-1", actor_role="owner", decision="approve",
            artifact_digest=fingerprint, policy_version="test-approval-v1",
        ),
        request_fingerprint=fingerprint,
    )
    request_ref = _seal_and_store(store, request)

    snapshot = SourceSnapshot(
        **_base_envelope_kwargs("SourceSnapshot"),
        repository_id="app-repo", canonical_path_ref=str(repo), git_revision=None, dirty=False,
        files=[],
    )
    snapshot_ref = _seal_and_store(store, snapshot)

    manifest = RepositoryManifest(
        **_base_envelope_kwargs("RepositoryManifest"),
        languages={"python": 1.0}, modules=["."], manifest_files=[], test_roots=["tests"],
        commands=[
            RepositoryCommand(
                command_id="unit-1", argv=[interpreter, "-m", "pytest", "tests"],
                working_directory=str(repo), kind="unit", source="pyproject_toml",
            )
        ],
        tool_coverage={"pytest_cov": 1.0} if declares_pytest_cov else {},
    )
    manifest_ref = _seal_and_store(store, manifest)

    baseline = BaselineSnapshot(
        **_base_envelope_kwargs("BaselineSnapshot"),
        request_digest=request_ref.content_digest,
        source_snapshot_digest=snapshot_ref.content_digest,
        workload_id="app-workload", environment_id="staging",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 1, tzinfo=UTC),
        aggregates=[
            MetricAggregate(
                metric_id="unit_command_result", unit="exit_code", sample_ids=["s1"], count=1,
                minimum=1.0 if baseline_unit_failing else 0.0,
                maximum=1.0 if baseline_unit_failing else 0.0,
                mean=1.0 if baseline_unit_failing else 0.0,
            ),
            MetricAggregate(
                metric_id="lint_command_result", unit="exit_code", sample_ids=["s1"], count=1,
                minimum=1.0 if baseline_lint_failing else 0.0,
                maximum=1.0 if baseline_lint_failing else 0.0,
                mean=1.0 if baseline_lint_failing else 0.0,
            ),
        ],
    )
    baseline_ref = _seal_and_store(store, baseline)

    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=_ZERO_DIGEST, quality_report_digest=_ZERO_DIGEST,
        strategies=[_strategy(before=before, after=after)],
    )
    portfolio_ref = _seal_and_store(store, portfolio)

    selected = SelectedSolution(
        **{
            **_base_envelope_kwargs("SelectedSolution"),
            # Pass-scoped to match real S01.90 output (BR-01-005); pass 0 is
            # a fresh case, which is what this fixture simulates.
            "artifact_id": f"{_CASE_ID}-S01.90-pass0-SelectedSolution",
        },
        ranking_result_digest=_ZERO_DIGEST, solution_portfolio_digest=portfolio_ref.content_digest,
        strategy_id="strategy-good",
        approval=SelectionApproval(decision="auto_selected", policy_version="s01-ranking-v1"),
    )
    selected_ref = _seal_and_store(store, selected)

    phase = ExecutionPhase(
        phase_id="phase-1", sequence=1, phase_kind="implementation", risk_tier="code",
        treatment=PlanTreatment(variable="compute_body", before=before, after=after),
        done_criteria=["correctness improves"], rollback_command="git checkout -- app.py",
        rollback_trigger="tests regress", rollback_deadline_seconds=600,
    )
    plan = ExecutionPlan(
        **_base_envelope_kwargs("ExecutionPlan"),
        selected_solution_digest=selected_ref.content_digest, phases=[phase],
    )
    plan_ref = _seal_and_store(store, plan)

    task = PlanTask(
        task_id="task-1", phase_id="phase-1", objective="Fix the arithmetic bug",
        files=["app.py"], instructions="Change 1 + 1 to 2 + 2 in app.py", owner="app-team",
    )
    task_list = TaskList(
        **_base_envelope_kwargs("TaskList"), execution_plan_digest=plan_ref.content_digest,
        tasks=[task],
    )
    task_list_ref = _seal_and_store(store, task_list)

    return [
        request_ref, snapshot_ref, manifest_ref, baseline_ref, portfolio_ref, selected_ref,
        plan_ref, task_list_ref,
    ]


def _state(refs: list[ArtifactRef]) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID, "thread_id": "THREAD-S04-1", "tenant_id": _TENANT, "lane": "manual",
        "artifact_refs": refs,
    }


def _run_s03(store: _MemoryArtifactStore, state: dict[str, Any]) -> dict[str, Any]:
    runtime = build_s03_runtime(ports=_ports(store))
    for node_id in S03_NODE_IDS:
        state = _advance(runtime, node_id, state)
    return state


def test_s04_registrations_cover_every_node() -> None:
    registrations = build_s04_registrations()
    assert set(registrations) == set(S04_NODE_IDS)


def test_s04_passes_a_correct_patch_and_seals_a_real_report(tmp_path: Path) -> None:
    # The repository's pre-existing bug (compute() returns 2, the real test
    # wants 4) is already present at baseline time -- A2 would have measured
    # it failing before any patch existed.
    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    store = _MemoryArtifactStore()
    refs = _seed_case(
        store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True,
    )
    state = _run_s03(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])
    assert workspace_path.exists()

    runtime = build_s04_runtime(ports=_ports(store))
    routes: dict[str, str] = {}
    for node_id in S04_NODE_IDS:
        state = _advance(runtime, node_id, state)
        routes[node_id] = state["node_routes"][node_id]

    assert routes["S04.10"] == "continue"
    assert routes["S04.80"] == "continue"

    report = _model_from_ref(store, _ref_by_type(state, "VerificationReport"), VerificationReport)
    assert report.passed is True
    unit_result = next(r for r in report.check_results if r.kind == "unit")
    assert unit_result.exit_code == 0
    assert report.failure_attributions == []

    assert state["s03_completed_task_ids"] == ["task-1"]
    # S04.90 no longer cleans up the workspace on a pass -- S05 (Controlled
    # Remeasurement) reuses this exact same workspace to rerun the workload,
    # so it must still be here; this test stops before S05, so it cleans up
    # manually instead of leaking a temp directory.
    assert workspace_path.exists()
    import shutil

    shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s04_reports_no_coverage_when_pytest_cov_is_not_declared(tmp_path: Path) -> None:
    """`coverage_percent` must stay honestly `None`, never fabricated, when
    the repository doesn't actually declare `pytest-cov` -- the default
    shape every other test in this file already exercises implicitly; this
    one asserts it explicitly."""

    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    store = _MemoryArtifactStore()
    refs = _seed_case(
        store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True,
    )
    state = _run_s03(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])

    runtime = build_s04_runtime(ports=_ports(store))
    for node_id in S04_NODE_IDS:
        state = _advance(runtime, node_id, state)

    report = _model_from_ref(store, _ref_by_type(state, "VerificationReport"), VerificationReport)
    unit_result = next(r for r in report.check_results if r.kind == "unit")
    assert unit_result.coverage_percent is None

    import shutil

    shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s04_reports_a_real_coverage_percent_when_pytest_cov_is_declared(tmp_path: Path) -> None:
    """When the manifest actually declares `pytest-cov`
    (`RepositoryManifest.tool_coverage`, set at real A2.30 detection), S04's
    unit check appends real `--cov` flags and parses a real percentage out
    of pytest-cov's own terminal report -- the spec's own S04.90 row
    ("Store commands, outputs, versions, durations, coverage and
    decision")."""

    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    store = _MemoryArtifactStore()
    refs = _seed_case(
        store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True,
        declares_pytest_cov=True, interpreter=sys.executable,
    )
    state = _run_s03(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])

    runtime = build_s04_runtime(ports=_ports(store))
    for node_id in S04_NODE_IDS:
        state = _advance(runtime, node_id, state)

    assert state["node_routes"]["S04.80"] == "continue"
    report = _model_from_ref(store, _ref_by_type(state, "VerificationReport"), VerificationReport)
    unit_result = next(r for r in report.check_results if r.kind == "unit")
    assert unit_result.coverage_percent is not None
    assert 0.0 <= unit_result.coverage_percent <= 100.0

    import shutil

    shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s04_fails_and_routes_revision_leaving_workspace_for_a_retry(tmp_path: Path) -> None:
    # The repository is healthy at baseline time (compute() correctly
    # returns 4, real test passes) -- the "optimization" treatment itself
    # is the bug, breaking a check that was real and passing before it.
    repo = _seed_repo(tmp_path, initial_expr="2 + 2")
    store = _MemoryArtifactStore()
    refs = _seed_case(
        store, repo=repo, before="2 + 2", after="1 + 1", baseline_unit_failing=False,
    )
    state = _run_s03(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])

    runtime = build_s04_runtime(ports=_ports(store))
    for node_id in S04_NODE_IDS:
        state = _advance(runtime, node_id, state)

    assert state["node_routes"]["S04.80"] == "revision"
    report = _model_from_ref(store, _ref_by_type(state, "VerificationReport"), VerificationReport)
    assert report.passed is False
    unit_result = next(r for r in report.check_results if r.kind == "unit")
    assert unit_result.exit_code != 0

    attribution = next(a for a in report.failure_attributions if a.check_kind == "unit")
    assert attribution.classification == "patch_regression"

    # Nothing promoted, workspace deliberately left for S03's real retry.
    assert state.get("s03_completed_task_ids") in (None, [])
    assert workspace_path.exists()

    import shutil

    shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s04_70_classifies_a_pre_existing_baseline_failure(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    # The lint command is a real, deterministic always-failing command,
    # standing in for a real static-analysis tool with a pre-existing
    # finding -- baseline already recorded it failing before any patch.
    store = _MemoryArtifactStore()
    refs = _seed_case(
        store, repo=repo, before="1 + 1", after="2 + 2",
        baseline_unit_failing=True, baseline_lint_failing=True,
    )
    # Add a real, always-failing "lint" command so S04.30 has something to
    # attribute against the baseline's already-failing lint aggregate.
    manifest_ref = _ref_by_type(_state(refs), "RepositoryManifest")
    manifest = _model_from_ref(store, manifest_ref, RepositoryManifest)
    manifest = manifest.model_copy(
        update={
            "commands": [
                *manifest.commands,
                RepositoryCommand(
                    command_id="lint-1", argv=["python", "-c", "import sys; sys.exit(1)"],
                    working_directory=str(repo), kind="lint", source="pyproject_toml",
                ),
            ]
        }
    )
    new_manifest_ref = _seal_and_store(store, manifest)
    refs = [new_manifest_ref if r.artifact_type == "RepositoryManifest" else r for r in refs]

    state = _run_s03(store, _state(refs))
    runtime = build_s04_runtime(ports=_ports(store))
    for node_id in S04_NODE_IDS:
        state = _advance(runtime, node_id, state)

    report = _model_from_ref(store, _ref_by_type(state, "VerificationReport"), VerificationReport)
    lint_attribution = next(a for a in report.failure_attributions if a.check_kind == "lint")
    assert lint_attribution.classification == "baseline_existing_failure"
    unit_attribution = next(
        (a for a in report.failure_attributions if a.check_kind == "unit"), None
    )
    assert unit_attribution is None  # the unit test itself passed this time

    if not report.passed:
        workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])
        import shutil

        shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s04_revision_route_really_reexecutes_s03_on_a_second_pass(tmp_path: Path) -> None:
    """Proves the real retry loop (BR-04-004): S04.80's "revision" route
    sends the graph back to S03.10, and a second real pass over
    S03.10..S04.90 must actually re-execute (not replay pass 1's cached
    `NodeExecution` via `NodeRuntime`'s own idempotency cache), seal a
    genuinely new, pass-scoped `PatchArtifact`/`ExecutionProvenance`/
    `VerificationReport` per pass without a `merge_artifact_refs` collision,
    and never let pass 1's stale check results leak into pass 2's gate."""

    repo = _seed_repo(tmp_path, initial_expr="2 + 2")
    store = _MemoryArtifactStore()
    refs = _seed_case(
        store, repo=repo, before="2 + 2", after="1 + 1", baseline_unit_failing=False,
    )
    state = _run_s03(store, _state(refs))
    runtime_s04 = build_s04_runtime(ports=_ports(store))
    for node_id in S04_NODE_IDS:
        state = _advance(runtime_s04, node_id, state)

    assert state["node_routes"]["S04.80"] == "revision"
    assert state["s03_revision_attempts"] == 1
    first_workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])
    assert first_workspace_path.exists()

    # The real "revision" edge (orchestration/shared_workflow.py) re-enters
    # S03.10 on the same, still-accumulated state.
    runtime_s03 = build_s03_runtime(ports=_ports(store))
    for node_id in S03_NODE_IDS:
        state = _advance(runtime_s03, node_id, state)
    for node_id in S04_NODE_IDS:
        state = _advance(runtime_s04, node_id, state)

    # Still fails (same bad treatment, nothing about the input changed) --
    # the point here is that it fails *for real*, a second time, not that it
    # was fixed. The route value is genuinely unchanged pass over pass, so
    # `_next_route_key` correctly reuses the same "S04.80" key rather than
    # growing a "#2" -- that reuse is itself the already-verified-correct
    # behavior, not a symptom of the bug this test targets.
    assert state["node_routes"]["S04.80"] == "revision"
    assert state["s03_revision_attempts"] == 2

    second_workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])
    assert second_workspace_path.exists()
    assert second_workspace_path != first_workspace_path
    assert not first_workspace_path.exists()  # S03.20 cleaned it up for real

    artifact_refs = cast("list[ArtifactRef]", state["artifact_refs"])
    patch_refs = [r for r in artifact_refs if r.artifact_type == "PatchArtifact"]
    provenance_refs = [r for r in artifact_refs if r.artifact_type == "ExecutionProvenance"]
    report_refs = [r for r in artifact_refs if r.artifact_type == "VerificationReport"]
    assert len(patch_refs) == 2
    assert len(provenance_refs) == 2
    assert len(report_refs) == 2
    assert len({r.artifact_id for r in patch_refs}) == 2
    assert len({r.artifact_id for r in report_refs}) == 2

    for ref in report_refs:
        report = _model_from_ref(store, ref, VerificationReport)
        # Pass 1 and pass 2 each ran exactly one real "unit" check -- if
        # `s04_check_results` were never reset between passes (the
        # accumulation bug this test also guards against), pass 2's report
        # would carry 2 (or more) "unit" results instead of exactly 1.
        unit_results = [r for r in report.check_results if r.kind == "unit"]
        assert len(unit_results) == 1
        assert unit_results[0].exit_code != 0

    import shutil

    shutil.rmtree(second_workspace_path.parent, ignore_errors=True)
