from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import (
    NodePorts,
    build_b1_runtime,
    build_b2_registrations,
    build_b2_runtime,
)
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.node_runtime import NodeRuntime
from production_optimizer.contracts.a2 import TrustLevel
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
from production_optimizer.contracts.b2 import ProposalEnvelope
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import (
    IntentRecord,
    IntentStatus,
    PolicyDecision,
    PolicyRequest,
)
from production_optimizer.orchestration.catalog import B2_NODE_IDS

_TENANT = "TENANT-B2"
_CASE_ID = "OPT-B2-1"
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


class _AllowPolicy:
    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=True, decision="allow", policy_version=request.policy_version, reasons=[]
        )

    def healthcheck(self) -> bool:
        return True


def _ports(store: _MemoryArtifactStore, *, workers: Any = None) -> NodePorts:
    return NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=workers
    )


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


def _seal_json(store: _MemoryArtifactStore, model: BaseModel, *, artifact_type: str) -> ArtifactRef:
    from production_optimizer.contracts.canonical import model_content_digest

    sealed = model.model_copy(update={"content_digest": model_content_digest(model)})
    content = canonical_json(sealed.model_dump(mode="json"))
    ref = ArtifactRef(
        artifact_type=artifact_type,
        schema_version="1.0",
        artifact_id=f"{_CASE_ID}-{artifact_type}",
        content_digest=sealed.content_digest,  # type: ignore[attr-defined]
        uri=f"memory://{_CASE_ID}-{artifact_type}",
    )
    store.seed_json(ref, content)
    return ref


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


def _seed_a3_outputs(
    store: _MemoryArtifactStore, *, risk_ceiling: str
) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef]:
    finding_set = FindingSet(
        **_base_envelope_kwargs("FindingSet"),
        evidence_bundle_digest=_ZERO_DIGEST,
        findings=[
            Finding(
                finding_id="finding-1",
                problem_signal_ids=["signal-1"],
                claim_type="hypothesis",
                symptom="a real symptom",
                causal_claim="a real causal claim",
                supporting_evidence_ids=["evidence-1"],
                confidence=0.7,
                trust_level=TrustLevel.T2,
                judgement=FindingJudgement(
                    finding_id="finding-1",
                    verdict="accept",
                    reasons=["ok"],
                    judge_id="judge-1",
                    model_id="test-model",
                    model_version="1.0",
                ),
            )
        ],
    )
    finding_set_ref = _seal_json(store, finding_set, artifact_type="FindingSet")

    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=finding_set_ref.content_digest,
        strategies=[
            SolutionStrategy(
                strategy_id="strategy-1",
                finding_ids=["finding-1"],
                title="Fix the thing",
                mechanism="Change the code to fix the thing",
                strategy_tradeoffs="Some tradeoff",
                phase_templates=[
                    ExperimentPhaseTemplate(
                        phase_id="phase-1",
                        sequence=1,
                        phase_kind="implementation",
                        treatment=Treatment(variable="x", before="broken", after="fixed"),
                    )
                ],
                risk_ceiling=cast("Any", risk_ceiling),
                risk_assessment=RiskAssessment(
                    assessment_id="risk-1",
                    strategy_id="strategy-1",
                    risk_tier=cast("Any", risk_ceiling),
                    blast_radius="single feature",
                    reversibility="fast",
                    uncertainty=0.2,
                    migration_impact=False,
                    security_impact=False,
                ),
                impact_assessment=ImpactAssessment(
                    assessment_id="impact-1",
                    strategy_id="strategy-1",
                    criterion_impacts=[
                        CriterionImpact(
                            criterion_id="auto-signal-real-1",
                            direction="improves",
                            confidence=0.6,
                            basis="forecast",
                        )
                    ],
                ),
                tradeoff_analysis=TradeoffAnalysis(
                    analysis_id="tradeoff-1",
                    strategy_id="strategy-1",
                    pros=["fixes the regression"],
                    cons=["needs a follow-up test"],
                    effort="low",
                    uncertainty=0.2,
                ),
                validation_plan=ValidationPlan(
                    plan_id="validation-1",
                    strategy_id="strategy-1",
                    benchmark_protocol="rerun the unit test suite",
                    stop_conditions=["test still fails"],
                ),
                rollback_plan=RollbackPlan(
                    plan_id="rollback-1",
                    strategy_id="strategy-1",
                    mechanism="revert the commit",
                    verification="rerun the unit test suite",
                    reversible=True,
                ),
                scope_resolution=ScopeResolutionReport(
                    report_id="scope-1",
                    strategy_id="strategy-1",
                    entries=[
                        ScopeResolutionEntry(
                            path_or_symbol="tests/test_ok.py", kind="file", exists=True
                        )
                    ],
                    fully_resolved=True,
                ),
                evidence_ids=["evidence-1"],
                eligible=True,
            )
        ],
        quality_report_digest=_ZERO_DIGEST,
    )
    portfolio_ref = _seal_json(store, portfolio, artifact_type="SolutionPortfolio")

    quality_report = A3QualityReport(
        **_base_envelope_kwargs("A3QualityReport"),
        finding_set_digest=finding_set_ref.content_digest,
        solution_portfolio_digest=portfolio_ref.content_digest,
        passed=True,
        portfolio_gate_results=[QualityGateResult(dimension="portfolio", passed=True)],
    )
    quality_report_ref = _seal_json(store, quality_report, artifact_type="A3QualityReport")

    return finding_set_ref, portfolio_ref, quality_report_ref


def test_b2_registrations_cover_every_native_node() -> None:
    registrations = build_b2_registrations()
    native = {node_id for node_id in B2_NODE_IDS if node_id != "B2.22"}
    assert set(registrations) == native


def _run_b1_to_qualified_opportunity(tmp_path: Path) -> tuple[_MemoryArtifactStore, dict[str, Any]]:
    """Real B1 -> real embedded A2, producing a genuine `QualifiedOpportunity`
    -- reused so B2's tests continue the *actual* state B1 hands off, not a
    hand-typed stand-in."""

    from production_optimizer.contracts.b1 import (
        DetectionSignal,
        RegisteredSource,
        RegisteredSourceSet,
    )
    from production_optimizer.orchestration.subgraphs import build_a2_graph

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"b2-fixture\"\n"
        "dependencies = [\"pytest-benchmark>=4.0\"]\n"
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (repo / "tests" / "test_ok_benchmark.py").write_text(
        "def test_ok_benchmark(benchmark):\n    benchmark(lambda: 1 + 1)\n"
    )

    store = _MemoryArtifactStore()
    # A distinct artifact_id ("seed-registry", not the default
    # f"{case_id}-RegisteredSourceSet") -- B1.20 re-seals its own copy under
    # that default id, which would otherwise collide with this seed input
    # under `merge_artifact_refs` the moment B1.20 actually runs.
    envelope_kwargs = _base_envelope_kwargs("RegisteredSourceSet")
    envelope_kwargs["artifact_id"] = "seed-registry"
    payload = RegisteredSourceSet(
        **envelope_kwargs,
        sources=[
            RegisteredSource(
                source_id="src-1",
                feature_id="checkout",
                repository_id="repo",
                local_path=str(repo),
            )
        ],
    )
    from production_optimizer.contracts.canonical import model_content_digest

    sealed_payload = payload.model_copy(update={"content_digest": model_content_digest(payload)})
    registry_ref = ArtifactRef(
        artifact_type="RegisteredSourceSet",
        schema_version="1.0",
        artifact_id="seed-registry",
        content_digest=sealed_payload.content_digest,
        uri="memory://seed-registry",
    )
    store.seed_json(registry_ref, canonical_json(sealed_payload.model_dump(mode="json")))

    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT)
    )
    try:
        ports = _ports(store, workers=broker)
        runtime = build_b1_runtime(ports=ports)
        state: dict[str, Any] = {
            "case_id": _CASE_ID,
            "thread_id": "THREAD-B2-1",
            "tenant_id": _TENANT,
            "entrypoint": "discovery",
            "lane": "automatic",
            "baseline_mode": "historical_recovery",
            "artifact_refs": [registry_ref],
        }
        for node_id in ("B1.10", "B1.20", "B1.21", "B1.30", "B1.31"):
            state = _advance(runtime, node_id, state)

        state = {
            **state,
            "b1_signals": [
                DetectionSignal(
                    signal_id="signal-real-1",
                    signal_kind="regression",
                    run_group_ids=["run-group-1"],
                    metric_id="unit_command_result",
                    description="unit test exit code regressed",
                    baseline_value=0.0,
                    observed_value=1.0,
                    effect_size=1.0,
                    unit="exit_code",
                    severity=0.9,
                    detected_at=datetime.now(UTC),
                )
            ],
        }
        for node_id in ("B1.60", "B1.61", "B1.62", "B1.70", "B1.71", "B1.81", "B1.90", "B1.91"):
            state = _advance(runtime, node_id, state)
        assert state["node_routes"]["B1.91"] == "continue"

        a2_result = build_a2_graph(runtime).invoke(state)
        state = {**state, **a2_result}
        assert "A2.95" in set(a2_result.get("completed_nodes", []))

        state = _advance(runtime, "B1.96", state)
        return store, state
    finally:
        broker.close()


def test_b2_auto_forwards_low_risk_portfolio_and_seals_real_envelope(tmp_path: Path) -> None:
    store, state = _run_b1_to_qualified_opportunity(tmp_path)
    finding_set_ref, portfolio_ref, quality_ref = _seed_a3_outputs(store, risk_ceiling="prompt")
    state = {
        **state,
        "artifact_refs": [*state["artifact_refs"], finding_set_ref, portfolio_ref, quality_ref],
    }

    runtime = build_b2_runtime(ports=_ports(store))
    for node_id in ("B2.10", "B2.20", "B2.21", "B2.30", "B2.31", "B2.40", "B2.41", "B2.50"):
        state = _advance(runtime, node_id, state)

    assert state["node_routes"]["B2.50"] == "continue"
    state = _advance(runtime, "B2.60", state)

    envelope = _model_from_ref(store, _ref_by_type(state, "ProposalEnvelope"), ProposalEnvelope)
    assert envelope.routing_decision.route == "auto_forward"
    assert envelope.approval.decision == "approved"
    assert envelope.qualified_opportunity_digest == _ref_by_type(
        state, "QualifiedOpportunity"
    ).content_digest


def test_b2_requires_owner_review_for_code_risk_and_fails_closed_without_a_decision(
    tmp_path: Path,
) -> None:
    store, state = _run_b1_to_qualified_opportunity(tmp_path)
    finding_set_ref, portfolio_ref, quality_ref = _seed_a3_outputs(store, risk_ceiling="code")
    state = {
        **state,
        "artifact_refs": [*state["artifact_refs"], finding_set_ref, portfolio_ref, quality_ref],
    }

    runtime = build_b2_runtime(ports=_ports(store))
    for node_id in ("B2.10", "B2.20", "B2.21", "B2.30", "B2.31", "B2.40", "B2.41", "B2.50"):
        state = _advance(runtime, node_id, state)

    assert state["node_routes"]["B2.50"] == "approval"
    assert state["b2_routing_decision"].route == "owner_review"

    # No real decision arrives (no resume_command) -- B2.51 must record
    # "pending", and B2.52 must fail closed to REJECTED, never CONTINUE.
    state = _advance(runtime, "B2.51", state)
    assert state["b2_approval"].decision == "pending"
    state = _advance(runtime, "B2.52", state)
    assert state["node_routes"]["B2.52"] == "rejected"
