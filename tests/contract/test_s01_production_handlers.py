from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application import NodePorts, build_s01_registrations, build_s01_runtime
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
from production_optimizer.contracts.c0 import ConvergedCase
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import IntentRecord, IntentStatus
from production_optimizer.contracts.s01 import RankingResult, SelectedSolution
from production_optimizer.orchestration.catalog import S01_NODE_IDS

_TENANT = "TENANT-S01"
_CASE_ID = "OPT-S01-1"
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
            artifact_type="JsonArtifact",
            schema_version="1.0",
            artifact_id=idempotency_key,
            content_digest=content_digest,
            uri=uri,
        )

    def put_blob(
        self, *, tenant_id: str, content: bytes, content_digest: str, media_type: str
    ) -> ArtifactRef:
        del tenant_id, media_type
        uri = f"memory://blob/{content_digest}"
        self._content_by_uri[uri] = content
        return ArtifactRef(
            artifact_type="Blob",
            schema_version="1.0",
            artifact_id=content_digest,
            content_digest=content_digest,
            uri=uri,
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


def _ports(store: _MemoryArtifactStore) -> NodePorts:
    return NodePorts(artifacts=store, intents=_MemoryIntentLedger())


def _advance(runtime: NodeRuntime, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    merged = dict(state)
    merged.update(result)
    merged["artifact_refs"] = [*existing_refs, *new_refs]
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
        artifact_type=artifact_type,
        schema_version="1.0",
        artifact_id=artifact_id,
        content_digest=digest,
        uri=f"memory://{artifact_id}",
    )
    store.seed_json(ref, canonical_json(sealed.model_dump(mode="json")))
    return ref


def _strategy(
    *,
    strategy_id: str,
    risk_ceiling: str,
    confidence: float,
    effort: str,
    reversibility: str,
    uncertainty: float,
    reversible: bool = True,
    fully_resolved: bool = True,
    eligible: bool = True,
) -> SolutionStrategy:
    return SolutionStrategy(
        strategy_id=strategy_id,
        finding_ids=["finding-1"],
        title=f"Strategy {strategy_id}",
        mechanism="Adjust the hot-path threshold to restore p95 latency",
        strategy_tradeoffs="Some tradeoff",
        phase_templates=[
            ExperimentPhaseTemplate(
                phase_id="phase-1",
                sequence=1,
                phase_kind="implementation",
                treatment=Treatment(variable="threshold", before="100", after="50"),
            )
        ],
        risk_ceiling=cast("Any", risk_ceiling),
        risk_assessment=RiskAssessment(
            assessment_id=f"risk-{strategy_id}",
            strategy_id=strategy_id,
            risk_tier=cast("Any", risk_ceiling),
            blast_radius="single feature",
            reversibility=cast("Any", reversibility),
            uncertainty=uncertainty,
            migration_impact=False,
            security_impact=False,
        ),
        impact_assessment=ImpactAssessment(
            assessment_id=f"impact-{strategy_id}",
            strategy_id=strategy_id,
            criterion_impacts=[
                CriterionImpact(
                    criterion_id="latency-p95",
                    direction="improves",
                    confidence=confidence,
                    basis="forecast",
                )
            ],
        ),
        tradeoff_analysis=TradeoffAnalysis(
            analysis_id=f"tradeoff-{strategy_id}",
            strategy_id=strategy_id,
            pros=["restores p95 latency"],
            cons=["needs a follow-up test"],
            effort=cast("Any", effort),
            uncertainty=uncertainty,
        ),
        validation_plan=ValidationPlan(
            plan_id=f"validation-{strategy_id}",
            strategy_id=strategy_id,
            benchmark_protocol="rerun the latency benchmark",
            stop_conditions=["latency still above target"],
        ),
        rollback_plan=RollbackPlan(
            plan_id=f"rollback-{strategy_id}",
            strategy_id=strategy_id,
            mechanism="revert the commit",
            verification="rerun the latency benchmark",
            reversible=reversible,
        ),
        scope_resolution=ScopeResolutionReport(
            report_id=f"scope-{strategy_id}",
            strategy_id=strategy_id,
            entries=[
                ScopeResolutionEntry(path_or_symbol="src/app.py", kind="file", exists=True)
            ],
            fully_resolved=fully_resolved,
        ),
        evidence_ids=["evidence-1"],
        eligible=eligible,
        gate_reasons=[] if eligible else ["not eligible for this test"],
    )


def _seed_case(
    store: _MemoryArtifactStore, *, best_risk_ceiling: str = "prompt", **best_kwargs: Any
) -> list[ArtifactRef]:
    fingerprint = sha256_digest(canonical_json({"case_id": _CASE_ID, "origin": "manual"}))
    request = OptimizationRequest(
        **_base_envelope_kwargs("OptimizationRequest"),
        origin=Origin.MANUAL,
        scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="checkout-repo",
            allowed_root_id="workspace",
            relative_path=".",
            requested_revision="HEAD",
        ),
        objective=Objective(statement="Reduce checkout p95 latency", feature_id="checkout"),
        criteria=[
            Criterion(
                criterion_id="latency-p95", metric_id="p95_latency_ms", direction="minimize",
                target=180.0, unit="ms", weight=2.0,
            ),
            Criterion(
                criterion_id="reliability", metric_id="error_rate", direction="minimize",
                target=0.01, unit="ratio", weight=1.0,
            ),
        ],
        workload=WorkloadContract(
            workload_id="checkout-workload", environment_id="staging", repetitions=3,
            warmup_runs=1, concurrency=1, cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="latency-samples", criterion_id="latency-p95",
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

    best_defaults: dict[str, Any] = {
        "confidence": 0.9, "effort": "low", "reversibility": "fast", "uncertainty": 0.1,
    }
    best_defaults.update(best_kwargs)
    good = _strategy(strategy_id="strategy-good", risk_ceiling=best_risk_ceiling, **best_defaults)
    bad = _strategy(
        strategy_id="strategy-bad", risk_ceiling="prompt", confidence=0.3, effort="high",
        reversibility="slow", uncertainty=0.5,
    )
    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=_ZERO_DIGEST,
        quality_report_digest=_ZERO_DIGEST,
        strategies=[good, bad],
    )
    portfolio_ref = _seal_and_store(store, portfolio)

    converged_case = ConvergedCase(
        **_base_envelope_kwargs("ConvergedCase"),
        origin="manual",
        request_digest=request_ref.content_digest,
        source_snapshot_digest=_ZERO_DIGEST,
        baseline_digest=_ZERO_DIGEST,
        evidence_bundle_digest=_ZERO_DIGEST,
        solution_portfolio_digest=portfolio_ref.content_digest,
        convergence_decision_digest=_ZERO_DIGEST,
    )
    converged_ref = _seal_and_store(store, converged_case)

    return [request_ref, portfolio_ref, converged_ref]


def _state(refs: list[ArtifactRef]) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID,
        "thread_id": "THREAD-S01-1",
        "tenant_id": _TENANT,
        "lane": "manual",
        "artifact_refs": refs,
    }


def test_s01_registrations_cover_every_node() -> None:
    registrations = build_s01_registrations()
    assert set(registrations) == set(S01_NODE_IDS)


def test_s01_auto_selects_the_clearly_better_low_risk_strategy() -> None:
    store = _MemoryArtifactStore()
    refs = _seed_case(store, best_risk_ceiling="prompt")
    runtime = build_s01_runtime(ports=_ports(store))
    state = _state(refs)

    routes: dict[str, str] = {}
    for node_id in S01_NODE_IDS:
        state = _advance(runtime, node_id, state)
        routes[node_id] = state["node_routes"][node_id]

    assert routes["S01.20"] == "continue"
    assert routes["S01.80"] == "continue"

    ranking = _model_from_ref(store, _ref_by_type(state, "RankingResult"), RankingResult)
    assert ranking.ranked_strategy_ids[0] == "strategy-good"

    selected = _model_from_ref(store, _ref_by_type(state, "SelectedSolution"), SelectedSolution)
    assert selected.strategy_id == "strategy-good"
    assert selected.approval.decision == "auto_selected"
    assert selected.ranking_result_digest == ranking.content_digest


def test_s01_halts_for_approval_on_a_code_risk_winner_and_is_resumable() -> None:
    store = _MemoryArtifactStore()
    refs = _seed_case(store, best_risk_ceiling="code")
    runtime = build_s01_runtime(ports=_ports(store))
    state = _state(refs)

    for node_id in ("S01.10", "S01.20", "S01.30", "S01.40", "S01.50", "S01.60", "S01.70"):
        state = _advance(runtime, node_id, state)
    state = _advance(runtime, "S01.80", state)

    # S01.80 always routes "continue" (the graph edge to S01.90 is taken
    # regardless) -- a real halt is represented by `pending_interrupt`, not
    # by the route, so S01.90 can run after a real resume and seal
    # `SelectedSolution` instead of being bypassed. See `s01_handlers`'s
    # S01.80/S01.90 node docstrings.
    assert state["node_routes"]["S01.80"] == "continue"
    interrupt = state["pending_interrupt"]
    assert interrupt is not None
    assert interrupt.stage == "S01.80"
    # RankingResult is sealed even though the run halted -- a real, resumable
    # interrupt (unlike B2.50/51's shape) always has something real to
    # reference by digest.
    ranking_ref = _ref_by_type(state, "RankingResult")
    assert interrupt.artifact_digest == ranking_ref.content_digest
    assert not any(ref.artifact_type == "SelectedSolution" for ref in state["artifact_refs"])


def test_s01_20_excludes_an_irreversible_strategy_but_keeps_a_real_fallback() -> None:
    store = _MemoryArtifactStore()
    refs = _seed_case(store, best_risk_ceiling="prompt", reversible=False)
    runtime = build_s01_runtime(ports=_ports(store))
    state = _state(refs)

    state = _advance(runtime, "S01.10", state)
    state = _advance(runtime, "S01.20", state)

    # Only "strategy-good" was made irreversible; "strategy-bad" remains a
    # real, eligible fallback -- BR-01-001 excludes the winner, not the case.
    assert state["node_routes"]["S01.20"] == "continue"
    assert state["s01_eligible_strategy_ids"] == ["strategy-bad"]


def test_s01_20_rejects_when_every_strategy_is_excluded() -> None:
    store = _MemoryArtifactStore()
    refs = _seed_case(store, best_risk_ceiling="prompt", reversible=False)
    runtime = build_s01_runtime(ports=_ports(store))
    state = _state(refs)
    # A prior Step 06 REVERT already excluded the only remaining fallback.
    state["s01_excluded_strategy_ids"] = ["strategy-bad"]

    state = _advance(runtime, "S01.10", state)
    state = _advance(runtime, "S01.20", state)

    assert state["node_routes"]["S01.20"] == "rejected"
    assert state["s01_eligible_strategy_ids"] == []
