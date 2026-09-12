from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application import NodePorts, build_c0_registrations, build_c0_runtime
from production_optimizer.application.node_runtime import NodeRuntime
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Guardrail,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.a2 import (
    BaselineSnapshot,
    EvidenceBundle,
    EvidenceIdentity,
    EvidenceItem,
    MetricAggregate,
    SourceSnapshot,
    TrustLevel,
)
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    CriterionImpact,
    ExperimentPhaseTemplate,
    Finding,
    FindingJudgement,
    FindingSet,
    ImpactAssessment,
    QualityGateResult,
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
from production_optimizer.contracts.c0 import ConvergedCase, ConvergenceDecision
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import IntentRecord, IntentStatus
from production_optimizer.orchestration.catalog import C0_NODE_IDS
from production_optimizer.orchestration.subgraphs import build_c0_graph

_TENANT = "TENANT-C0"
_CASE_ID = "OPT-C0-1"
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


def _seed_manual_case(
    store: _MemoryArtifactStore,
    *,
    metric_id: str = "p95_latency_ms",
    request_digest_override: str | None = None,
    observed_at: datetime | None = None,
) -> list[ArtifactRef]:
    """Seed a complete, correctly digest-linked "manual" (Lane A shaped) case
    directly into the store -- same accepted pattern as
    `test_b2_production_handlers._seed_a3_outputs`, extended to cover every
    artifact type C0 verifies. Callers can pass `request_digest_override` to
    deliberately break the BaselineSnapshot->OptimizationRequest link for a
    negative test."""

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
                criterion_id="latency-p95",
                metric_id=metric_id,
                direction="minimize",
                target=180.0,
                unit="ms",
                weight=1.0,
            )
        ],
        guardrails=[
            Guardrail(
                guardrail_id="correctness", metric_id="unit_tests", operator="eq", threshold=1.0,
                unit="pass",
            )
        ],
        workload=WorkloadContract(
            workload_id="checkout-workload",
            environment_id="staging",
            repetitions=3,
            warmup_runs=1,
            concurrency=1,
            cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="latency-samples",
                criterion_id="latency-p95",
                accepted_source_types={"benchmark"},
                minimum_samples=1,
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

    source = SourceSnapshot(
        **_base_envelope_kwargs("SourceSnapshot"),
        repository_id="checkout-repo",
        canonical_path_ref="/nonexistent/checkout-repo",
        git_revision=None,
        dirty=False,
        files=[],
    )
    source_ref = _seal_and_store(store, source)

    baseline = BaselineSnapshot(
        **_base_envelope_kwargs("BaselineSnapshot"),
        request_digest=request_digest_override or request_ref.content_digest,
        source_snapshot_digest=source_ref.content_digest,
        workload_id="checkout-workload",
        environment_id="staging",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 1, tzinfo=UTC),
        aggregates=[
            MetricAggregate(
                metric_id=metric_id, unit="ms", sample_ids=["s1", "s2", "s3"], count=3,
                minimum=190.0, maximum=230.0, mean=210.0,
            )
        ],
    )
    baseline_ref = _seal_and_store(store, baseline)

    evidence_observed_at = observed_at or datetime.now(UTC)
    bundle = EvidenceBundle(
        **_base_envelope_kwargs("EvidenceBundle"),
        # Real A2 (`a2_handlers._a2_80`) sets this to the *source snapshot*
        # digest, not the baseline's: EvidenceBundle is sealed before
        # BaselineSnapshot exists in the real pipeline.
        baseline_digest=source_ref.content_digest,
        evidence=[
            EvidenceItem(
                evidence_id="evidence-1",
                evidence_type="benchmark_sample",
                trust_level=TrustLevel.T3,
                identity=EvidenceIdentity(
                    feature_id="checkout",
                    repository_id="checkout-repo",
                    source_snapshot_digest=source_ref.content_digest,
                    workload_id="checkout-workload",
                    environment_id="staging",
                    hardware_profile="ci-standard",
                    concurrency=1,
                    cache_state="warm",
                    metric_schema_version="1.0",
                    sample_id="s1",
                    observed_at=evidence_observed_at,
                    collector="pytest-benchmark",
                    collector_version="4.0",
                ),
                raw_ref=ArtifactRef(
                    artifact_type="RawEvidence", schema_version="1.0", artifact_id="raw-1",
                    content_digest=_ZERO_DIGEST, uri="memory://raw-1",
                ),
                value=210.0,
                unit="ms",
            )
        ],
        collector_versions={"pytest-benchmark": "4.0"},
        coverage={"latency-p95": 1.0},
    )
    bundle_ref = _seal_and_store(store, bundle)

    finding_set = FindingSet(
        **_base_envelope_kwargs("FindingSet"),
        evidence_bundle_digest=bundle_ref.content_digest,
        findings=[
            Finding(
                finding_id="finding-1",
                problem_signal_ids=["signal-1"],
                claim_type="hypothesis",
                symptom="p95 latency regressed",
                causal_claim="an unbounded loop increases latency under load",
                supporting_evidence_ids=["evidence-1"],
                confidence=0.7,
                trust_level=TrustLevel.T2,
                judgement=FindingJudgement(
                    finding_id="finding-1", verdict="accept", reasons=["evidence supports claim"],
                    judge_id="judge-1", model_id="test-model", model_version="1.0",
                ),
            )
        ],
    )
    finding_set_ref = _seal_and_store(store, finding_set)

    quality_report = A3QualityReport(
        **_base_envelope_kwargs("A3QualityReport"),
        finding_set_digest=finding_set_ref.content_digest,
        solution_portfolio_digest=_ZERO_DIGEST,
        passed=True,
        portfolio_gate_results=[QualityGateResult(dimension="portfolio", passed=True)],
    )
    quality_ref = _seal_and_store(store, quality_report)

    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=finding_set_ref.content_digest,
        quality_report_digest=quality_ref.content_digest,
        strategies=[
            SolutionStrategy(
                strategy_id="strategy-1",
                finding_ids=["finding-1"],
                title="Bound the loop",
                mechanism="Cap iteration count to restore p95 latency",
                strategy_tradeoffs="Slightly reduces worst-case coverage",
                phase_templates=[
                    ExperimentPhaseTemplate(
                        phase_id="phase-1", sequence=1, phase_kind="implementation",
                        treatment=Treatment(
                            variable="loop_bound", before="unbounded", after="1000"
                        ),
                    )
                ],
                risk_ceiling=cast("Any", "prompt"),
                risk_assessment=RiskAssessment(
                    assessment_id="risk-1", strategy_id="strategy-1",
                    risk_tier=cast("Any", "prompt"), blast_radius="single feature",
                    reversibility="fast", uncertainty=0.2, migration_impact=False,
                    security_impact=False,
                ),
                impact_assessment=ImpactAssessment(
                    assessment_id="impact-1", strategy_id="strategy-1",
                    criterion_impacts=[
                        CriterionImpact(
                            criterion_id="latency-p95", direction="improves", confidence=0.6,
                            basis="forecast",
                        )
                    ],
                ),
                tradeoff_analysis=TradeoffAnalysis(
                    analysis_id="tradeoff-1", strategy_id="strategy-1",
                    pros=["restores p95 latency"], cons=["needs a follow-up test"], effort="low",
                    uncertainty=0.2,
                ),
                validation_plan=ValidationPlan(
                    plan_id="validation-1", strategy_id="strategy-1",
                    benchmark_protocol="rerun the latency benchmark",
                    stop_conditions=["latency still above target"],
                ),
                rollback_plan=RollbackPlan(
                    plan_id="rollback-1", strategy_id="strategy-1", mechanism="revert the commit",
                    verification="rerun the latency benchmark", reversible=True,
                ),
                scope_resolution=ScopeResolutionReport(
                    report_id="scope-1", strategy_id="strategy-1",
                    entries=[
                        ScopeResolutionEntry(
                            path_or_symbol="src/checkout/service.py", kind="file", exists=True
                        )
                    ],
                    fully_resolved=True,
                ),
                evidence_ids=["evidence-1"],
                eligible=True,
            )
        ],
    )
    portfolio_ref = _seal_and_store(store, portfolio)

    return [
        request_ref, source_ref, baseline_ref, bundle_ref, finding_set_ref, quality_ref,
        portfolio_ref,
    ]


def _manual_state(refs: list[ArtifactRef]) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID,
        "thread_id": "THREAD-C0-1",
        "tenant_id": _TENANT,
        "lane": "manual",
        "entrypoint": "manual",
        "baseline_mode": "active_collection",
        "artifact_refs": refs,
    }


def test_c0_registrations_cover_every_node() -> None:
    registrations = build_c0_registrations()
    assert set(registrations) == set(C0_NODE_IDS)


def test_c0_converges_a_real_manual_case_end_to_end() -> None:
    store = _MemoryArtifactStore()
    refs = _seed_manual_case(store)
    runtime = build_c0_runtime(ports=_ports(store))
    state = _manual_state(refs)

    for node_id in ("C0.10", "C0.20", "C0.30", "C0.40", "C0.50"):
        state = _advance(runtime, node_id, state)

    schema_results = state["c0_schema_results"]
    assert all(result.valid for result in schema_results)
    digest_chain = state["c0_digest_chain"]
    assert all(link.linked for link in digest_chain)
    equivalence_verdicts = state["c0_equivalence_verdicts"]
    assert all(verdict.equivalent for verdict in equivalence_verdicts)
    freshness_checks = state["c0_freshness_checks"]
    assert all(check.fresh for check in freshness_checks)

    state = _advance(runtime, "C0.60", state)
    assert state["node_routes"]["C0.60"] == "continue"
    decision = _model_from_ref(
        store, _ref_by_type(state, "ConvergenceDecision"), ConvergenceDecision
    )
    assert decision.converged is True
    assert decision.eligible_solution_count == 1
    assert decision.reasons == []

    state = _advance(runtime, "C0.70", state)
    assert state["status"] == "converged"
    converged_case = _model_from_ref(
        store, _ref_by_type(state, "ConvergedCase"), ConvergedCase
    )
    request_ref = _ref_by_type(state, "OptimizationRequest")
    assert converged_case.request_digest == request_ref.content_digest
    assert converged_case.convergence_decision_digest == decision.content_digest
    assert state["convergence_ref"].artifact_type == "ConvergedCase"


def test_c0_10_fails_closed_on_origin_mismatch() -> None:
    """BR-C0-001: a case whose lane disagrees with its sealed
    OptimizationRequest.origin must never proceed past C0.10."""

    store = _MemoryArtifactStore()
    refs = _seed_manual_case(store)
    runtime = build_c0_runtime(ports=_ports(store))
    state = _manual_state(refs)
    state["lane"] = "automatic"  # OptimizationRequest.origin was sealed as "manual"

    try:
        _advance(runtime, "C0.10", state)
    except ValueError as exc:
        assert "BR-C0-001" in str(exc)
    else:
        raise AssertionError("expected a BR-C0-001 ValueError")


def test_c0_30_detects_a_broken_digest_chain_and_c0_60_rejects() -> None:
    store = _MemoryArtifactStore()
    wrong_digest = f"sha256:{'1' * 64}"
    refs = _seed_manual_case(store, request_digest_override=wrong_digest)
    runtime = build_c0_runtime(ports=_ports(store))
    state = _manual_state(refs)

    for node_id in ("C0.10", "C0.20", "C0.30", "C0.40", "C0.50"):
        state = _advance(runtime, node_id, state)

    digest_chain = state["c0_digest_chain"]
    broken = [link for link in digest_chain if not link.linked]
    assert len(broken) == 1
    assert broken[0].artifact_type == "BaselineSnapshot"

    state = _advance(runtime, "C0.60", state)
    assert state["node_routes"]["C0.60"] == "rejected"
    decision = _model_from_ref(
        store, _ref_by_type(state, "ConvergenceDecision"), ConvergenceDecision
    )
    assert decision.converged is False
    assert "digest chain is broken" in " ".join(decision.reasons)


def test_c0_40_detects_a_baseline_collected_for_a_different_workload() -> None:
    """Real semantic equivalence, not a per-metric check: A2's aggregates are
    keyed by `EvidenceItem.evidence_type` (a raw collector label like
    "unit_command_result"), which a real run never maps 1:1 against a
    criterion's business `metric_id` -- confirmed by actually running
    `scripts/optimize.py` end to end, where a placeholder performance
    criterion legitimately has no matching aggregate. What C0.40 must catch
    for real is a baseline stamped with a workload/environment that does not
    match what the request actually asked for."""

    store = _MemoryArtifactStore()
    refs = _seed_manual_case(store)
    source_ref = refs[1]
    mismatched_baseline = BaselineSnapshot(
        **_base_envelope_kwargs("BaselineSnapshot"),
        request_digest=refs[0].content_digest,
        source_snapshot_digest=source_ref.content_digest,
        workload_id="a-different-workload",
        environment_id="production",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 1, tzinfo=UTC),
        aggregates=[
            MetricAggregate(
                metric_id="unit_command_result", unit="exit_code", sample_ids=["s1"], count=1,
                minimum=1.0, maximum=1.0, mean=1.0,
            )
        ],
    )
    baseline_ref = _seal_and_store(store, mismatched_baseline)
    refs = [refs[0], refs[1], baseline_ref, *refs[3:]]

    runtime = build_c0_runtime(ports=_ports(store))
    state = _manual_state(refs)
    for node_id in ("C0.10", "C0.20", "C0.30", "C0.40"):
        state = _advance(runtime, node_id, state)

    verdicts = state["c0_equivalence_verdicts"]
    failed = [v for v in verdicts if not v.equivalent]
    assert len(failed) == 1
    assert failed[0].dimension == "request_baseline_contract"
    assert "a-different-workload" in (failed[0].reason or "")


def test_c0_50_detects_stale_evidence_and_c0_60_rejects() -> None:
    store = _MemoryArtifactStore()
    stale_at = datetime.now(UTC) - timedelta(days=30)
    refs = _seed_manual_case(store, observed_at=stale_at)
    runtime = build_c0_runtime(ports=_ports(store))
    state = _manual_state(refs)

    for node_id in ("C0.10", "C0.20", "C0.30", "C0.40", "C0.50"):
        state = _advance(runtime, node_id, state)

    freshness_checks = state["c0_freshness_checks"]
    evidence_check = next(c for c in freshness_checks if c.dimension == "evidence")
    assert evidence_check.fresh is False
    assert evidence_check.expires_at is None

    state = _advance(runtime, "C0.60", state)
    assert state["node_routes"]["C0.60"] == "rejected"


def test_c0_graph_routes_a_rejected_case_straight_to_end() -> None:
    store = _MemoryArtifactStore()
    wrong_digest = f"sha256:{'2' * 64}"
    refs = _seed_manual_case(store, request_digest_override=wrong_digest)
    runtime = build_c0_runtime(ports=_ports(store))
    graph = build_c0_graph(runtime)

    result = graph.invoke(cast("Any", _manual_state(refs)))
    assert "C0.70" not in set(result.get("completed_nodes", []))
    assert result["node_routes"]["C0.60"] == "rejected"
