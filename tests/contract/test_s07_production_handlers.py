from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application import (
    NodePorts,
    build_s03_runtime,
    build_s04_runtime,
    build_s05_runtime,
    build_s06_runtime,
    build_s07_registrations,
    build_s07_runtime,
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
from production_optimizer.contracts.s07 import OptimizationReport
from production_optimizer.orchestration.catalog import (
    S03_NODE_IDS,
    S04_NODE_IDS,
    S05_NODE_IDS,
    S06_NODE_IDS,
    S07_NODE_IDS,
)

_TENANT = "TENANT-S07"
_CASE_ID = "OPT-S07-1"
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


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def compute():\n    return 1 + 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "from app import compute\n\n\ndef test_compute():\n    assert compute() == 4\n"
    )
    return repo


def _strategy() -> SolutionStrategy:
    return SolutionStrategy(
        strategy_id="strategy-good",
        finding_ids=["finding-1"],
        title="Fix compute",
        mechanism="Correct the arithmetic in compute()",
        strategy_tradeoffs="None",
        phase_templates=[
            ExperimentPhaseTemplate(
                phase_id="phase-1", sequence=1, phase_kind="implementation",
                treatment=Treatment(variable="compute_body", before="1 + 1", after="2 + 2"),
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


def _seed_case(store: _MemoryArtifactStore, *, repo: Path) -> list[ArtifactRef]:
    fingerprint = sha256_digest(canonical_json({"case_id": _CASE_ID, "origin": "manual"}))
    correctness = Criterion(
        criterion_id="correctness", metric_id="unit_command_result", direction="minimize",
        target=0.0, unit="exit_code", weight=1.0,
    )
    request = OptimizationRequest(
        **_base_envelope_kwargs("OptimizationRequest"),
        origin=Origin.MANUAL, scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="app-repo", allowed_root_id="workspace", relative_path=".",
            requested_revision="HEAD",
        ),
        objective=Objective(statement="Fix compute()", feature_id="app"),
        criteria=[correctness],
        workload=WorkloadContract(
            workload_id="app-workload", environment_id="staging", repetitions=1, warmup_runs=0,
            concurrency=1, cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="correctness-samples", criterion_id="correctness",
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
                minimum=1.0, maximum=1.0, mean=1.0,
            ),
        ],
    )
    baseline_ref = _seal_and_store(store, baseline)

    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=_ZERO_DIGEST, quality_report_digest=_ZERO_DIGEST,
        strategies=[_strategy()],
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
        phase_id="phase-1", sequence=1, phase_kind="implementation",
        treatment=PlanTreatment(variable="compute_body", before="1 + 1", after="2 + 2"),
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
        "case_id": _CASE_ID, "thread_id": "THREAD-S07-1", "tenant_id": _TENANT, "lane": "manual",
        "artifact_refs": refs,
    }


def _run_through_s06(store: _MemoryArtifactStore, state: dict[str, Any]) -> dict[str, Any]:
    for node_ids, builder in (
        (S03_NODE_IDS, build_s03_runtime),
        (S04_NODE_IDS, build_s04_runtime),
        (S05_NODE_IDS, build_s05_runtime),
        (S06_NODE_IDS, build_s06_runtime),
    ):
        runtime = builder(ports=_ports(store))
        for node_id in node_ids:
            state = _advance(runtime, node_id, state)
            if state["node_routes"][node_id] == "rejected":
                raise AssertionError(f"{node_id} unexpectedly rejected: {state}")
    assert state["s06_decision"]["outcome"] == "KEEP"
    return state


def test_s07_registrations_cover_every_node() -> None:
    registrations = build_s07_registrations()
    assert set(registrations) == set(S07_NODE_IDS)


def test_s07_publishes_a_real_report_for_a_kept_case(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    store = _MemoryArtifactStore()
    refs = _seed_case(store, repo=repo)
    state = _run_through_s06(store, _state(refs))

    runtime = build_s07_runtime(ports=_ports(store))
    routes: dict[str, str] = {}
    for node_id in S07_NODE_IDS:
        state = _advance(runtime, node_id, state)
        routes[node_id] = state["node_routes"][node_id]

    assert routes["S07.10"] == "continue"
    assert routes["S07.90"] == "continue"

    report_ref = _ref_by_type(state, "OptimizationReport")
    report = _model_from_ref(store, report_ref, OptimizationReport)
    assert report.case_outcome == "KEEP"
    assert report.phase_id == "phase-1"

    # No repair pass happened -- exactly one outcome entry, for the sole
    # criterion, and it is "completed" (the bug really was fixed).
    assert len(report.outcomes) == 1
    assert report.outcomes[0].subject == "correctness"
    assert report.outcomes[0].category == "completed"

    assert len(report.evidence) == 1
    assert report.evidence[0].baseline_mean == 1.0  # baseline: the bug was already failing
    assert report.evidence[0].treatment_mean == 0.0  # real rerun of "unit" after the patch
    assert report.evidence[0].met is True

    assert report.source_change.changed_files == ["app.py"]
    assert report.source_change.rollback_command == "git checkout -- app.py"
    assert report.source_change.rollback_executed is False

    # The deterministic (non-LLM) S03 executor was used, so no real model
    # tokens were spent -- a real, honest zero, not a fabricated number.
    assert report.cost.model_input_tokens == 0
    assert report.cost.model_output_tokens == 0
    assert report.cost.phase_repair_attempts == 0

    assert len(report.narrative) > 0

    # The published JSON/Markdown blobs are real and independently
    # verifiable (content-addressed, matching store.verify()).
    json_ref = ArtifactRef(
        artifact_type="Blob", schema_version="1.0", artifact_id=report.json_report_digest,
        content_digest=report.json_report_digest, uri=f"memory://blob/{report.json_report_digest}",
    )
    assert store.verify(tenant_id=_TENANT, ref=json_ref)
    markdown_ref = ArtifactRef(
        artifact_type="Blob", schema_version="1.0", artifact_id=report.markdown_report_digest,
        content_digest=report.markdown_report_digest,
        uri=f"memory://blob/{report.markdown_report_digest}",
    )
    assert store.verify(tenant_id=_TENANT, ref=markdown_ref)
    markdown_text = store.read(tenant_id=_TENANT, ref=markdown_ref).decode("utf-8")
    assert "# Optimization Report" in markdown_text
    assert "KEEP" in markdown_text
