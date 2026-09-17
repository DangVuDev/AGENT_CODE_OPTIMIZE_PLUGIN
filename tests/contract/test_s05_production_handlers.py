from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application import (
    NodePorts,
    build_s03_runtime,
    build_s04_runtime,
    build_s05_registrations,
    build_s05_runtime,
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
    FileIdentity,
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
from production_optimizer.contracts.s05 import Measurement, StatisticalReport
from production_optimizer.orchestration.catalog import S03_NODE_IDS, S04_NODE_IDS, S05_NODE_IDS

_TENANT = "TENANT-S05"
_CASE_ID = "OPT-S05-1"
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
    baseline_unit_failing: bool, extra_criteria: list[Criterion] | None = None,
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
            ),
            *(extra_criteria or []),
        ],
        workload=WorkloadContract(
            workload_id="app-workload", environment_id="staging", repetitions=1, warmup_runs=0,
            concurrency=1, cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="unit-samples", criterion_id="correctness",
                accepted_source_types={"benchmark"}, minimum_samples=1,
            ),
            *(
                EvidenceRequirement(
                    requirement_id=f"{c.criterion_id}-samples", criterion_id=c.criterion_id,
                    accepted_source_types={"benchmark"}, minimum_samples=1,
                )
                for c in (extra_criteria or [])
            ),
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
                command_id="unit-1", argv=["python", "-m", "pytest", "tests"],
                working_directory=str(repo), kind="unit", source="pyproject_toml",
            )
        ],
        tool_coverage={},
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
        "case_id": _CASE_ID, "thread_id": "THREAD-S05-1", "tenant_id": _TENANT, "lane": "manual",
        "artifact_refs": refs,
    }


def _run_s03_and_s04(store: _MemoryArtifactStore, state: dict[str, Any]) -> dict[str, Any]:
    runtime_s03 = build_s03_runtime(ports=_ports(store))
    for node_id in S03_NODE_IDS:
        state = _advance(runtime_s03, node_id, state)
    runtime_s04 = build_s04_runtime(ports=_ports(store))
    for node_id in S04_NODE_IDS:
        state = _advance(runtime_s04, node_id, state)
    assert state["node_routes"]["S04.80"] == "continue"
    return state


def test_s05_registrations_cover_every_node() -> None:
    registrations = build_s05_registrations()
    assert set(registrations) == set(S05_NODE_IDS)


def test_s05_seals_a_real_comparable_measurement_with_correct_effects(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    store = _MemoryArtifactStore()
    refs = _seed_case(
        store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True,
    )
    state = _run_s03_and_s04(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])
    assert workspace_path.exists()

    runtime = build_s05_runtime(ports=_ports(store))
    routes: dict[str, str] = {}
    for node_id in S05_NODE_IDS:
        state = _advance(runtime, node_id, state)
        routes[node_id] = state["node_routes"][node_id]

    assert routes["S05.30"] == "continue"
    assert routes["S05.70"] == "continue"

    statistical = _model_from_ref(
        store, _ref_by_type(state, "StatisticalReport"), StatisticalReport
    )
    effect = next(e for e in statistical.effects if e.criterion_id == "correctness")
    assert effect.baseline_mean == 1.0  # baseline: the bug was already failing
    assert effect.treatment_mean == 0.0  # a real rerun of "unit" in the patched workspace

    measurement = _model_from_ref(store, _ref_by_type(state, "Measurement"), Measurement)
    assert measurement.phase_id == "phase-1"

    # S05.40 is the real, final consumer of S03's isolated workspace now
    # that S04.90 no longer cleans it up on a pass (see both docstrings).
    assert not workspace_path.exists()


def test_s05_70_rejects_when_a_criterion_cannot_be_measured(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    store = _MemoryArtifactStore()
    unmeasurable_criterion = Criterion(
        criterion_id="latency", metric_id="p95_latency_ms", direction="minimize", target=100.0,
        unit="ms", weight=1.0,
    )
    refs = _seed_case(
        store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True,
        extra_criteria=[unmeasurable_criterion],
    )
    state = _run_s03_and_s04(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])

    runtime = build_s05_runtime(ports=_ports(store))
    for node_id in S05_NODE_IDS:
        state = _advance(runtime, node_id, state)
        if state["node_routes"][node_id] == "rejected":
            break  # mirrors the real graph's add_routed_edge(..., "rejected": END)

    assert state["node_routes"]["S05.70"] == "rejected"
    assert "Measurement" not in {
        r.artifact_type for r in cast("list[ArtifactRef]", state["artifact_refs"])
    }
    treatment = cast("dict[str, Any]", state["s05_treatment_aggregates"])
    assert treatment["unmeasurable_criterion_ids"] == ["latency"]

    # S05.40 already ran (and cleaned up) before the S05.70 rejection --
    # nothing left to clean up manually here.
    assert not workspace_path.exists()


def test_s05_60_fails_quality_when_baseline_has_no_aggregate_for_a_criterions_metric(
    tmp_path: Path,
) -> None:
    """A criterion whose metric_id can genuinely be measured this pass
    (S05.40 successfully reruns a real repository-owned command for it) but
    has no matching aggregate in A2's real `BaselineSnapshot` must fail
    sample quality -- there is no honest baseline to compare against, so
    `_s05_80` must never fabricate one. This is a real, different case from
    "no command could be resolved at all" (already covered by
    `test_s05_70_rejects_when_a_criterion_cannot_be_measured`)."""

    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    (repo / "lint_ok.py").write_text("import sys\nsys.exit(0)\n")
    store = _MemoryArtifactStore()
    criterion_without_baseline = Criterion(
        criterion_id="lint-quality", metric_id="lint_command_result", direction="minimize",
        target=0.0, unit="exit_code", weight=1.0,
    )
    refs = _seed_case(
        store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True,
        extra_criteria=[criterion_without_baseline],
    )
    manifest_ref = _ref_by_type(_state(refs), "RepositoryManifest")
    manifest = _model_from_ref(store, manifest_ref, RepositoryManifest)
    manifest = manifest.model_copy(
        update={
            "commands": [
                *manifest.commands,
                RepositoryCommand(
                    command_id="lint-1", argv=["python", "lint_ok.py"],
                    working_directory=str(repo), kind="lint", source="pyproject_toml",
                ),
            ]
        }
    )
    new_manifest_ref = _seal_and_store(store, manifest)
    refs = [new_manifest_ref if r.artifact_type == "RepositoryManifest" else r for r in refs]

    state = _run_s03_and_s04(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])

    runtime = build_s05_runtime(ports=_ports(store))
    for node_id in S05_NODE_IDS:
        state = _advance(runtime, node_id, state)
        if state["node_routes"][node_id] == "rejected":
            break

    # The criterion really was measurable this pass (S05.40 did not mark
    # it unmeasurable) -- the failure is specifically the missing baseline.
    treatment = cast("dict[str, Any]", state["s05_treatment_aggregates"])
    assert "lint-quality" not in treatment["unmeasurable_criterion_ids"]
    assert "lint-quality" in treatment["by_criterion"]

    quality = cast("dict[str, Any]", state["s05_quality"])
    assert quality["passed"] is False
    assert any("lint-quality" in reason and "no baseline aggregate" in reason
               for reason in quality["reasons"])
    assert state["node_routes"]["S05.70"] == "rejected"
    assert "Measurement" not in {
        r.artifact_type for r in cast("list[ArtifactRef]", state["artifact_refs"])
    }

    if workspace_path.exists():
        import shutil

        shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s05_20_detects_dependency_drift_outside_the_patch(tmp_path: Path) -> None:
    """A tracked manifest file (e.g. requirements.txt) whose content
    changes between A2.30's snapshot and this remeasurement pass -- without
    the patch itself ever touching it -- is real drift from something else
    (a CI-side dependency upgrade, say). BR-05-001 must catch this even
    though no source file the patch changed is involved."""

    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    original_requirements = "requests==2.31.0\n"
    (repo / "requirements.txt").write_text(original_requirements)
    store = _MemoryArtifactStore()
    refs = _seed_case(store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True)

    snapshot_ref = _ref_by_type(_state(refs), "SourceSnapshot")
    snapshot = _model_from_ref(store, snapshot_ref, SourceSnapshot)
    snapshot = snapshot.model_copy(
        update={
            "files": [
                FileIdentity(
                    relative_path="requirements.txt",
                    content_digest=sha256_digest(original_requirements.encode("utf-8")),
                )
            ]
        }
    )
    new_snapshot_ref = _seal_and_store(store, snapshot)
    refs = [new_snapshot_ref if r.artifact_type == "SourceSnapshot" else r for r in refs]

    manifest_ref = _ref_by_type(_state(refs), "RepositoryManifest")
    manifest = _model_from_ref(store, manifest_ref, RepositoryManifest)
    manifest = manifest.model_copy(update={"manifest_files": ["requirements.txt"]})
    new_manifest_ref = _seal_and_store(store, manifest)
    refs = [new_manifest_ref if r.artifact_type == "RepositoryManifest" else r for r in refs]

    state = _run_s03_and_s04(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])

    # Simulate a CI-side dependency upgrade that happened between A2.30's
    # snapshot and this remeasurement pass -- the patch itself never
    # touched this file.
    (workspace_path / "requirements.txt").write_text("requests==2.32.0\n")

    runtime = build_s05_runtime(ports=_ports(store))
    for node_id in S05_NODE_IDS:
        state = _advance(runtime, node_id, state)
        if state["node_routes"][node_id] == "rejected":
            break

    assert state["node_routes"]["S05.20"] == "continue"  # S05.20 itself never routes rejected
    isolation_ref = next(
        r for r in cast("list[ArtifactRef]", state["artifact_refs"])
        if r.artifact_type == "IsolationReport"
    )
    from production_optimizer.contracts.s05 import IsolationReport

    isolation = _model_from_ref(store, isolation_ref, IsolationReport)
    assert isolation.isolated is False
    violation = next(v for v in isolation.violations if v.kind == "dependency_drift")
    assert "requirements.txt" in violation.detail

    if workspace_path.exists():
        import shutil

        shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s05_20_does_not_double_flag_a_manifest_file_the_patch_itself_changed(
    tmp_path: Path,
) -> None:
    """A manifest file the patch legitimately owns changing is already
    caught by the existing changed_files/manifest_files intersection check
    -- the new content-digest check must not also flag it a second time."""

    repo = _seed_repo(tmp_path, initial_expr="1 + 1")
    original_requirements = "requests==2.31.0\n"
    (repo / "requirements.txt").write_text(original_requirements)
    store = _MemoryArtifactStore()
    refs = _seed_case(store, repo=repo, before="1 + 1", after="2 + 2", baseline_unit_failing=True)

    snapshot_ref = _ref_by_type(_state(refs), "SourceSnapshot")
    snapshot = _model_from_ref(store, snapshot_ref, SourceSnapshot)
    snapshot = snapshot.model_copy(
        update={
            "files": [
                FileIdentity(
                    relative_path="requirements.txt",
                    content_digest=sha256_digest(original_requirements.encode("utf-8")),
                )
            ]
        }
    )
    new_snapshot_ref = _seal_and_store(store, snapshot)
    refs = [new_snapshot_ref if r.artifact_type == "SourceSnapshot" else r for r in refs]

    manifest_ref = _ref_by_type(_state(refs), "RepositoryManifest")
    manifest = _model_from_ref(store, manifest_ref, RepositoryManifest)
    manifest = manifest.model_copy(update={"manifest_files": ["requirements.txt"]})
    new_manifest_ref = _seal_and_store(store, manifest)
    refs = [new_manifest_ref if r.artifact_type == "RepositoryManifest" else r for r in refs]

    # Add a task/file so S03 actually patches requirements.txt itself --
    # only app.py is scoped in _seed_case's own TaskList, so patch it here
    # by editing app.py (the real scoped file) plus requirements.txt
    # directly in the source repo before S03 runs, then let S03 copy it
    # into the workspace unmodified (simulating S03 legitimately owning
    # this change is out of scope for this test -- what matters is the
    # workspace's requirements.txt already differs from source at S03.20
    # copy time, so patch.changed_files will include it).
    (repo / "requirements.txt").write_text("requests==2.32.0\n")

    state = _run_s03_and_s04(store, _state(refs))
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])

    runtime = build_s05_runtime(ports=_ports(store))
    for node_id in S05_NODE_IDS:
        state = _advance(runtime, node_id, state)
        if state["node_routes"][node_id] == "rejected":
            break

    isolation_ref = next(
        r for r in cast("list[ArtifactRef]", state["artifact_refs"])
        if r.artifact_type == "IsolationReport"
    )
    from production_optimizer.contracts.s05 import IsolationReport

    isolation = _model_from_ref(store, isolation_ref, IsolationReport)
    drift_violations = [v for v in isolation.violations if v.kind == "dependency_drift"]
    # Exactly one violation for requirements.txt, not two (the intersection
    # check and the digest check must not both fire for the same cause).
    assert len(drift_violations) == 1

    if workspace_path.exists():
        import shutil

        shutil.rmtree(workspace_path.parent, ignore_errors=True)
