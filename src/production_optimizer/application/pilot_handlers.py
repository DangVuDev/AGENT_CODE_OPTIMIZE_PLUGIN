from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
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
    ComparabilityReport,
    DimensionVerdict,
    EvidenceBundle,
    EvidenceIdentity,
    EvidenceItem,
    EvidenceQualityReport,
    FileIdentity,
    MetricAggregate,
    RepositoryCommand,
    RepositoryManifest,
    SourceSnapshot,
    TrustLevel,
)
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    CitationResolutionEntry,
    CitationResolutionReport,
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
from production_optimizer.contracts.b1 import (
    CooldownDecision,
    DetectionReport,
    DetectionSignal,
    FeatureBinding,
    OpportunityScore,
    OwnershipBinding,
    QualificationDecision,
    QualifiedOpportunity,
    RunGroup,
    SourceBinding,
)
from production_optimizer.contracts.b2 import (
    ProposalApproval,
    ProposalEnvelope,
    ProposalRoutingDecision,
    StalenessDecision,
)
from production_optimizer.contracts.c0 import (
    ConvergedCase,
    ConvergenceDecision,
    DigestChainLink,
    DimensionEquivalenceVerdict,
    FreshnessCheck,
    SchemaValidationResult,
)
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import ALL_BUSINESS_NODE_IDS

_ZERO_DIGEST = f"sha256:{'0' * 64}"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_PRODUCER = ProducerIdentity(name="pilot-flow-handler", version="1.0.0")


def build_pilot_runtime() -> NodeRuntime:
    """Register deterministic handlers for every documented business node.

    The pilot runtime exercises the complete LangGraph topology using strict
    Pydantic artifacts and compact state references. It is intentionally
    deterministic and local: it does not execute repository commands, query
    telemetry systems, call a model, dispatch workers or mutate source.
    """

    return NodeRuntime(build_pilot_registrations())


def build_pilot_registrations() -> dict[str, RegisteredNode]:
    """Return deterministic pilot registrations without constructing a runtime."""

    return {
        node_id: RegisteredNode(spec=_node_spec(node_id), handler=_handler_for(node_id))
        for node_id in sorted(ALL_BUSINESS_NODE_IDS)
    }


def _node_spec(node_id: str) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="pilot-flow",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="pilot-v1",
        side_effect_class=SideEffectClass.PURE,
        timeout_seconds=5,
        max_attempts=1,
        allowed_routes=_allowed_routes(node_id),
        runbook=f"docs/implementation/{_runbook(node_id)}",
        slo="deterministic pilot flow completes in-process",
    )


def _allowed_routes(node_id: str) -> set[str]:
    routes = {NodeRoute.CONTINUE.value}
    match node_id:
        case "A1.80":
            routes |= {NodeRoute.CLARIFICATION.value, NodeRoute.REJECTED.value}
        case "A1.90" | "B1.91" | "B2.50":
            routes |= {NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value}
        case "A2.90":
            routes |= {NodeRoute.MISSING.value, NodeRoute.REJECTED.value}
        case "A2.91":
            routes |= {NodeRoute.MISSING.value, NodeRoute.INCOMPARABLE.value}
        case "A3.81" | "B2.52":
            routes |= {NodeRoute.REVISION.value, NodeRoute.REJECTED.value}
        case "B1.71":
            routes |= {NodeRoute.QUARANTINE.value, NodeRoute.REJECTED.value}
        case "B1.80":
            routes |= {NodeRoute.MERGED.value, NodeRoute.SUPPRESSED.value}
        case "B1.81":
            routes |= {NodeRoute.SUPPRESSED.value, NodeRoute.CLOSED.value}
        case "B2.31":
            routes |= {NodeRoute.REFRESH.value, NodeRoute.REJECTED.value}
        case "C0.60":
            routes |= {NodeRoute.REJECTED.value}
        case _:
            pass
    return routes


def _runbook(node_id: str) -> str:
    if node_id.startswith(("A1.", "A2.", "A3.", "C0.")):
        return "05-lane-1-detailed-implementation-playbook.md"
    return "06-lane-2-detailed-implementation-playbook.md"


def _handler_for(node_id: str) -> Callable[[OptimizationState, NodePorts | None], NodeExecution]:
    def execute(state: OptimizationState, _ports: NodePorts | None, /) -> NodeExecution:
        return _execute_node(node_id, state)

    execute.__name__ = f"pilot_{node_id.replace('.', '_').lower()}"
    return execute


def _execute_node(node_id: str, state: OptimizationState) -> NodeExecution:
    updates: dict[str, Any] = {}
    refs: list[ArtifactRef] = []

    if node_id == "A1.95":
        ref = _optimization_request_ref(state)
        updates["request_ref"] = ref
        refs.append(ref)
    elif node_id in {"A2.20", "A2.30", "A2.70", "A2.80", "A2.90", "A2.91", "A2.95"}:
        refs.extend(_a2_refs_for(node_id, state))
        if node_id == "A2.95":
            updates["baseline_ref"] = _first_ref(refs, "BaselineSnapshot")
    elif node_id in {"A3.51", "A3.81", "A3.90"}:
        refs.extend(_a3_refs_for(node_id, state))
        if node_id == "A3.90":
            updates["solution_portfolio_ref"] = _first_ref(refs, "SolutionPortfolio")
    elif node_id in {"B1.41", "B1.90", "B1.96"}:
        refs.extend(_b1_refs_for(node_id, state))
        if node_id == "B1.90":
            updates["request_ref"] = _first_ref(refs, "OptimizationRequest")
    elif node_id == "B2.60":
        refs.extend(_b2_refs_for(node_id, state))
    elif node_id in {"C0.60", "C0.70"}:
        refs.extend(_c0_refs_for(node_id, state))
        if node_id == "C0.70":
            updates["convergence_ref"] = _first_ref(refs, "ConvergedCase")
            updates["status"] = "converged"

    if refs:
        updates["artifact_refs"] = refs

    return NodeExecution(route=NodeRoute.CONTINUE, updates=updates)


def _first_ref(refs: list[ArtifactRef], artifact_type: str) -> ArtifactRef:
    return next(ref for ref in refs if ref.artifact_type == artifact_type)


def _seal[TEnvelope: ArtifactEnvelope](model: TEnvelope) -> TEnvelope:
    digest = model_content_digest(model)
    return model.model_copy(update={"content_digest": digest})


def _ref(model: ArtifactEnvelope) -> ArtifactRef:
    return ArtifactRef(
        artifact_type=model.artifact_type,
        schema_version=model.schema_version,
        artifact_id=model.artifact_id,
        content_digest=model.content_digest,
        uri=f"urn:production-optimizer:pilot:{model.tenant_id}:{model.artifact_id}",
    )


def _artifact_ref(
    *,
    state: OptimizationState,
    artifact_type: str,
    artifact_id: str,
    payload: Mapping[str, Any],
) -> ArtifactRef:
    content_digest = sha256_digest(canonical_json(payload))
    return ArtifactRef(
        artifact_type=artifact_type,
        schema_version="1.0",
        artifact_id=artifact_id,
        content_digest=content_digest,
        uri=f"urn:production-optimizer:pilot:{_tenant_id(state)}:{artifact_id}",
    )


def _state_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef | None:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    return None


def _digest_of(state: OptimizationState, artifact_type: str, fallback: str = _ZERO_DIGEST) -> str:
    ref = _state_ref(state, artifact_type)
    return ref.content_digest if ref is not None else fallback


def _tenant_id(state: OptimizationState) -> str:
    return state.get("tenant_id", "TENANT-PILOT")


def _case_id(state: OptimizationState) -> str:
    return state.get("case_id", "CASE-PILOT")


def _origin(state: OptimizationState) -> str:
    return "manual" if state.get("lane") == "manual" else "automatic"


def _request_origin(state: OptimizationState) -> Origin:
    return Origin.MANUAL if _origin(state) == "manual" else Origin.AUTOMATIC


def _base_envelope(state: OptimizationState, artifact_type: str) -> dict[str, Any]:
    return {
        "artifact_id": f"{_case_id(state)}-{artifact_type}",
        "tenant_id": _tenant_id(state),
        "case_id": _case_id(state),
        "created_at": _NOW,
        "producer": _PRODUCER,
        "content_digest": _ZERO_DIGEST,
        "policy_versions": {"pilot": "1.0.0"},
    }


def _optimization_request_ref(state: OptimizationState) -> ArtifactRef:
    request = _optimization_request(state)
    return _ref(request)


def _optimization_request(state: OptimizationState) -> OptimizationRequest:
    fingerprint = _artifact_digest({"case_id": _case_id(state), "origin": _origin(state)})
    request = OptimizationRequest(
        **_base_envelope(state, "OptimizationRequest"),
        origin=_request_origin(state),
        scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="pilot-repository",
            allowed_root_id="workspace",
            relative_path=".",
            requested_revision="pilot-revision",
        ),
        objective=Objective(
            statement="Reduce p95 latency for the pilot feature without correctness regression",
            feature_id="pilot-feature",
        ),
        criteria=[
            Criterion(
                criterion_id="latency-p95",
                metric_id="p95_latency_ms",
                direction="minimize",
                target=180.0,
                unit="ms",
                weight=1.0,
            )
        ],
        guardrails=[
            Guardrail(
                guardrail_id="correctness",
                metric_id="unit_tests",
                operator="eq",
                threshold=1.0,
                unit="pass",
            )
        ],
        workload=WorkloadContract(
            workload_id="pilot-workload",
            dataset_id="pilot-dataset",
            environment_id="pilot-env",
            command_id="benchmark-pilot",
            repetitions=3,
            warmup_runs=1,
            concurrency=1,
            cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="latency-samples",
                criterion_id="latency-p95",
                accepted_source_types={"benchmark", "telemetry"},
                minimum_samples=3,
                mandatory=True,
            )
        ],
        budget=ExecutionBudget(
            deadline_seconds=300,
            maximum_worker_seconds=120,
            maximum_model_tokens=2048,
            maximum_storage_bytes=10_000_000,
            allowed_analyzers={"syntax", "runtime"},
        ),
        approval=ApprovalBinding(
            approval_id=f"approval-{_case_id(state)}",
            actor_id="pilot-owner",
            actor_role="owner",
            decision="approve",
            artifact_digest=fingerprint,
            policy_version="pilot-approval-v1",
        ),
        request_fingerprint=fingerprint,
    )
    return _seal(request)


def _a2_refs_for(node_id: str, state: OptimizationState) -> list[ArtifactRef]:
    match node_id:
        case "A2.20":
            return [_ref(_source_snapshot(state))]
        case "A2.30":
            return [_ref(_repository_manifest(state))]
        case "A2.70":
            return [
                _artifact_ref(
                    state=state,
                    artifact_type="RawEvidence",
                    artifact_id=f"{_case_id(state)}-RawEvidence",
                    payload={"node_id": node_id, "samples": [220.0, 210.0, 230.0]},
                )
            ]
        case "A2.80":
            return [_ref(_evidence_bundle(state))]
        case "A2.90":
            return [_ref(_evidence_quality_report(state))]
        case "A2.91":
            return [_ref(_comparability_report(state))]
        case "A2.95":
            return [_ref(_baseline_snapshot(state))]
        case _:
            return []


def _source_snapshot(state: OptimizationState) -> SourceSnapshot:
    snapshot = SourceSnapshot(
        **_base_envelope(state, "SourceSnapshot"),
        repository_id="pilot-repository",
        canonical_path_ref="workspace:.",
        git_revision="pilot-revision",
        dirty=False,
        files=[
            FileIdentity(
                relative_path="src/pilot.py",
                content_digest=_artifact_digest({"file": "src/pilot.py"}),
            )
        ],
    )
    return _seal(snapshot)


def _repository_manifest(state: OptimizationState) -> RepositoryManifest:
    manifest = RepositoryManifest(
        **_base_envelope(state, "RepositoryManifest"),
        languages={"python": 1.0},
        modules=["pilot"],
        manifest_files=["pyproject.toml"],
        test_roots=["tests"],
        commands=[
            RepositoryCommand(
                command_id="benchmark-pilot",
                argv=["python", "-m", "pytest"],
                working_directory=".",
                kind="benchmark",
                source="pyproject_toml",
            )
        ],
        tool_coverage={"syntax": 1.0, "runtime": 1.0},
    )
    return _seal(manifest)


def _baseline_snapshot(state: OptimizationState) -> BaselineSnapshot:
    baseline = BaselineSnapshot(
        **_base_envelope(state, "BaselineSnapshot"),
        request_digest=_digest_of(state, "OptimizationRequest"),
        source_snapshot_digest=_digest_of(state, "SourceSnapshot"),
        workload_id="pilot-workload",
        environment_id="pilot-env",
        window_start=_NOW,
        window_end=_NOW + timedelta(minutes=5),
        aggregates=[
            MetricAggregate(
                metric_id="p95_latency_ms",
                unit="ms",
                sample_ids=["sample-1", "sample-2", "sample-3"],
                count=3,
                minimum=210.0,
                maximum=230.0,
                mean=220.0,
                percentile_50=220.0,
                percentile_95=230.0,
            )
        ],
    )
    return _seal(baseline)


def _evidence_bundle(state: OptimizationState) -> EvidenceBundle:
    identity = EvidenceIdentity(
        feature_id="pilot-feature",
        repository_id="pilot-repository",
        source_snapshot_digest=_digest_of(state, "SourceSnapshot"),
        workload_id="pilot-workload",
        dataset_id="pilot-dataset",
        environment_id="pilot-env",
        hardware_profile="pilot-local",
        concurrency=1,
        cache_state="warm",
        metric_schema_version="latency-v1",
        sample_id="sample-1",
        observed_at=_NOW,
        collector="pilot-collector",
        collector_version="1.0.0",
    )
    raw_ref = _state_ref(state, "RawEvidence") or _artifact_ref(
        state=state,
        artifact_type="RawEvidence",
        artifact_id=f"{_case_id(state)}-RawEvidence",
        payload={"samples": [220.0, 210.0, 230.0]},
    )
    bundle = EvidenceBundle(
        **_base_envelope(state, "EvidenceBundle"),
        baseline_digest=_digest_of(state, "BaselineSnapshot"),
        evidence=[
            EvidenceItem(
                evidence_id="evidence-latency-p95",
                evidence_type="benchmark",
                trust_level=TrustLevel.T3,
                identity=identity,
                raw_ref=raw_ref,
                value=230.0,
                unit="ms",
            )
        ],
        collector_versions={"pilot-collector": "1.0.0"},
        coverage={"latency-p95": 1.0},
    )
    return _seal(bundle)


def _evidence_quality_report(state: OptimizationState) -> EvidenceQualityReport:
    report = EvidenceQualityReport(
        **_base_envelope(state, "EvidenceQualityReport"),
        passed=True,
        mandatory_coverage={"latency-samples": True},
        sample_failures=[],
        freshness_failures=[],
        integrity_failures=[],
        redaction_failures=[],
        collector_failures={},
    )
    return _seal(report)


def _comparability_report(state: OptimizationState) -> ComparabilityReport:
    report = ComparabilityReport(
        **_base_envelope(state, "ComparabilityReport"),
        comparable=True,
        dimensions=[
            DimensionVerdict(
                dimension="environment",
                comparable=True,
                baseline_value="pilot-env",
                expected_value="pilot-env",
                material=True,
                reason="pilot fixture uses a single pinned environment",
            )
        ],
        policy_version="pilot-comparability-v1",
    )
    return _seal(report)


def _a3_refs_for(node_id: str, state: OptimizationState) -> list[ArtifactRef]:
    if node_id == "A3.51":
        return [_ref(_finding_set(state))]
    if node_id == "A3.81":
        return [_ref(_a3_quality_report(state))]
    if node_id == "A3.90":
        return [_ref(_solution_portfolio(state))]
    return []


def _finding_set(state: OptimizationState) -> FindingSet:
    judgement = FindingJudgement(
        judge_id="pilot-judge",
        finding_id="finding-p95-latency",
        verdict="accept",
        reasons=["pilot evidence demonstrates an observable latency gap"],
        model_id="deterministic-pilot",
        model_version="1.0.0",
    )
    finding = Finding(
        finding_id="finding-p95-latency",
        problem_signal_ids=["signal-p95-latency"],
        claim_type="observation",
        symptom="p95 latency is above the pilot target",
        scope_files=["src/pilot.py"],
        causal_claim="pilot analysis identifies a bounded optimization candidate",
        supporting_evidence_ids=["evidence-latency-p95"],
        analyzer_coverage={"syntax": 1.0, "runtime": 1.0},
        confidence=0.7,
        trust_level=TrustLevel.T3,
        judgement=judgement,
    )
    citation = CitationResolutionReport(
        report_id="citation-finding-p95-latency",
        finding_id="finding-p95-latency",
        entries=[
            CitationResolutionEntry(
                evidence_id="evidence-latency-p95",
                resolved=True,
                in_scope=True,
                supports_statement=True,
            )
        ],
        all_resolved=True,
    )
    finding_set = FindingSet(
        **_base_envelope(state, "FindingSet"),
        evidence_bundle_digest=_digest_of(state, "EvidenceBundle"),
        findings=[finding],
        citation_reports=[citation],
    )
    return _seal(finding_set)


def _solution_portfolio(state: OptimizationState) -> SolutionPortfolio:
    strategy = _solution_strategy()
    portfolio = SolutionPortfolio(
        **_base_envelope(state, "SolutionPortfolio"),
        finding_set_digest=_digest_of(state, "FindingSet"),
        strategies=[strategy],
        quality_report_digest=_digest_of(state, "A3QualityReport"),
    )
    return _seal(portfolio)


def _solution_strategy() -> SolutionStrategy:
    strategy_id = "strategy-pilot-cache-hot-path"
    risk = RiskAssessment(
        assessment_id="risk-pilot",
        strategy_id=strategy_id,
        risk_tier="code",
        blast_radius="pilot feature path",
        reversibility="fast",
        uncertainty=0.3,
        migration_impact=False,
        security_impact=False,
    )
    impact = ImpactAssessment(
        assessment_id="impact-pilot",
        strategy_id=strategy_id,
        criterion_impacts=[
            CriterionImpact(
                criterion_id="latency-p95",
                direction="improves",
                confidence=0.65,
                basis="forecast",
                magnitude=40.0,
                unit="ms",
            )
        ],
    )
    tradeoff = TradeoffAnalysis(
        analysis_id="tradeoff-pilot",
        strategy_id=strategy_id,
        pros=["lower p95 latency on the pilot workload"],
        cons=["requires validation against correctness guardrails"],
        effort="medium",
        uncertainty=0.3,
        evidence_ids=["evidence-latency-p95"],
    )
    validation = ValidationPlan(
        plan_id="validation-pilot",
        strategy_id=strategy_id,
        test_command_ids=["benchmark-pilot"],
        benchmark_protocol="run pilot workload after the change and compare p95 latency",
        expected_metric_movements={"p95_latency_ms": "decrease"},
        stop_conditions=["unit_tests must remain pass", "p95 latency must not regress"],
    )
    rollback = RollbackPlan(
        plan_id="rollback-pilot",
        strategy_id=strategy_id,
        mechanism="revert the single pilot treatment",
        verification="rerun benchmark-pilot",
        reversible=True,
    )
    scope = ScopeResolutionReport(
        report_id="scope-pilot",
        strategy_id=strategy_id,
        entries=[
            ScopeResolutionEntry(
                path_or_symbol="src/pilot.py",
                kind="file",
                exists=True,
            )
        ],
        fully_resolved=True,
    )
    phase = ExperimentPhaseTemplate(
        phase_id="phase-pilot-1",
        sequence=1,
        phase_kind="implementation",
        treatment=Treatment(
            variable="hot-path lookup",
            before="compute on every call",
            after="reuse a bounded cached value",
        ),
        expected_observations=["p95 latency decreases on the pilot workload"],
    )
    return SolutionStrategy(
        strategy_id=strategy_id,
        finding_ids=["finding-p95-latency"],
        title="Cache the pilot hot-path lookup",
        mechanism="Introduce a bounded cache around the measured hot-path lookup.",
        strategy_tradeoffs="Improves latency at the cost of cache invalidation complexity.",
        phase_templates=[phase],
        risk_ceiling="code",
        risk_assessment=risk,
        impact_assessment=impact,
        tradeoff_analysis=tradeoff,
        validation_plan=validation,
        rollback_plan=rollback,
        scope_resolution=scope,
        evidence_ids=["evidence-latency-p95"],
        eligible=True,
    )


def _a3_quality_report(state: OptimizationState) -> A3QualityReport:
    result = QualityGateResult(dimension="portfolio", passed=True, detail="pilot gate")
    report = A3QualityReport(
        **_base_envelope(state, "A3QualityReport"),
        finding_set_digest=_digest_of(state, "FindingSet"),
        solution_portfolio_digest=_digest_of(state, "SolutionPortfolio"),
        passed=True,
        finding_gate_results=[result],
        strategy_gate_results=[result],
        cause_maturity_gate_results=[result],
        portfolio_gate_results=[result],
    )
    return _seal(report)


def _b1_refs_for(node_id: str, state: OptimizationState) -> list[ArtifactRef]:
    if node_id == "B1.41":
        return [_ref(_detection_report(state))]
    if node_id == "B1.90":
        return [_optimization_request_ref(state)]
    if node_id == "B1.96":
        return [_ref(_qualified_opportunity(state))]
    return []


def _detection_report(state: OptimizationState) -> DetectionReport:
    run_group = RunGroup(
        group_id="run-group-pilot",
        feature_id="pilot-feature",
        workload_id="pilot-workload",
        dataset_id="pilot-dataset",
        environment_id="pilot-env",
        sample_ids=["sample-1", "sample-2", "sample-3"],
        window_start=_NOW,
        window_end=_NOW + timedelta(hours=1),
        trust_level="T3",
    )
    signal = DetectionSignal(
        signal_id="signal-p95-latency",
        signal_kind="regression",
        run_group_ids=[run_group.group_id],
        metric_id="p95_latency_ms",
        description="pilot p95 latency regressed across compatible windows",
        baseline_value=180.0,
        observed_value=230.0,
        effect_size=50.0,
        unit="ms",
        severity=0.7,
        detected_at=_NOW + timedelta(hours=1),
    )
    report = DetectionReport(
        **_base_envelope(state, "DetectionReport"),
        scan_id=_case_id(state),
        window_start=_NOW,
        window_end=_NOW + timedelta(hours=1),
        run_groups=[run_group],
        signals=[signal],
        feature_bindings=[
            FeatureBinding(
                binding_id="feature-binding-pilot",
                signal_ids=[signal.signal_id],
                feature_id="pilot-feature",
                confidence=1.0,
                method="explicit_label",
                resolved=True,
            )
        ],
        source_bindings=[
            SourceBinding(
                binding_id="source-binding-pilot",
                repository_id="pilot-repository",
                git_revision="pilot-revision",
                resolved=True,
            )
        ],
        ownership_bindings=[
            OwnershipBinding(
                binding_id="owner-binding-pilot",
                code_owner="pilot-owner",
                service_owner="pilot-owner",
                decision_owner="pilot-owner",
                resolved=True,
            )
        ],
        scores=[
            OpportunityScore(
                score_id="score-pilot",
                severity=0.7,
                frequency=0.6,
                business_impact=0.5,
                evidence_trust=0.8,
                addressability=0.7,
                strategic_priority=0.5,
                composite=0.65,
                policy_version="pilot-score-v1",
            )
        ],
        qualification_decisions=[
            QualificationDecision(
                decision_id="qualification-pilot",
                qualified=True,
                policy_version="pilot-qualification-v1",
            )
        ],
        cooldown_decisions=[CooldownDecision(decision_id="cooldown-pilot", suppressed=False)],
    )
    return _seal(report)


def _qualified_opportunity(state: OptimizationState) -> QualifiedOpportunity:
    opportunity = QualifiedOpportunity(
        **_base_envelope(state, "QualifiedOpportunity"),
        detection_report_digest=_digest_of(state, "DetectionReport"),
        request_digest=_digest_of(state, "OptimizationRequest"),
        source_snapshot_digest=_digest_of(state, "SourceSnapshot"),
        baseline_digest=_digest_of(state, "BaselineSnapshot"),
        evidence_bundle_digest=_digest_of(state, "EvidenceBundle"),
        comparability_report_digest=_digest_of(state, "ComparabilityReport"),
        source_binding=SourceBinding(
            binding_id="source-binding-pilot",
            repository_id="pilot-repository",
            git_revision="pilot-revision",
            resolved=True,
        ),
        owner_binding=OwnershipBinding(
            binding_id="owner-binding-pilot",
            code_owner="pilot-owner",
            service_owner="pilot-owner",
            decision_owner="pilot-owner",
            resolved=True,
        ),
        case_start_id=f"case-start-{_case_id(state)}",
    )
    return _seal(opportunity)


def _b2_refs_for(node_id: str, state: OptimizationState) -> list[ArtifactRef]:
    if node_id == "B2.60":
        return [_ref(_proposal_envelope(state, narrative="Pilot proposal accepted for C0."))]
    return []


def _proposal_envelope(state: OptimizationState, *, narrative: str | None) -> ProposalEnvelope:
    proposal = ProposalEnvelope(
        **_base_envelope(state, "ProposalEnvelope"),
        qualified_opportunity_digest=_digest_of(state, "QualifiedOpportunity"),
        finding_set_digest=_digest_of(state, "FindingSet"),
        solution_portfolio_digest=_digest_of(state, "SolutionPortfolio"),
        quality_report_digest=_digest_of(state, "A3QualityReport"),
        routing_decision=ProposalRoutingDecision(
            decision_id="routing-pilot",
            route="auto_forward",
            reasons=["pilot policy allows automatic C0 handoff"],
            policy_version="pilot-routing-v1",
        ),
        approval=ProposalApproval(
            approval_id="proposal-approval-pilot",
            actor_id="pilot-owner",
            actor_role="owner",
            decision="approved",
            artifact_digest=_digest_of(state, "SolutionPortfolio"),
            policy_version="pilot-proposal-approval-v1",
        ),
        staleness=StalenessDecision(
            decision_id="staleness-pilot",
            stale=False,
            action="proceed",
        ),
        narrative=narrative,
    )
    return _seal(proposal)


def _c0_refs_for(node_id: str, state: OptimizationState) -> list[ArtifactRef]:
    if node_id == "C0.60":
        return [_ref(_convergence_decision(state))]
    if node_id == "C0.70":
        return [_ref(_converged_case(state))]
    return []


def _convergence_decision(state: OptimizationState) -> ConvergenceDecision:
    checked_at = _NOW + timedelta(minutes=10)
    decision = ConvergenceDecision(
        **_base_envelope(state, "ConvergenceDecision"),
        origin=cast("Any", _origin(state)),
        schema_results=[
            SchemaValidationResult(
                artifact_type="OptimizationRequest",
                schema_version="1.0",
                valid=True,
            ),
            SchemaValidationResult(
                artifact_type="BaselineSnapshot",
                schema_version="1.0",
                valid=True,
            ),
            SchemaValidationResult(
                artifact_type="SolutionPortfolio",
                schema_version="1.0",
                valid=True,
            ),
        ],
        digest_chain=[
            DigestChainLink(
                artifact_type="OptimizationRequest",
                artifact_digest=_digest_of(state, "OptimizationRequest"),
                linked=True,
            ),
            DigestChainLink(
                artifact_type="BaselineSnapshot",
                artifact_digest=_digest_of(state, "BaselineSnapshot"),
                expected_parent_digest=_digest_of(state, "OptimizationRequest"),
                linked=True,
            ),
            DigestChainLink(
                artifact_type="SolutionPortfolio",
                artifact_digest=_digest_of(state, "SolutionPortfolio"),
                expected_parent_digest=_digest_of(state, "FindingSet"),
                linked=True,
            ),
        ],
        equivalence_verdicts=[
            DimensionEquivalenceVerdict(
                dimension="request-baseline-solution",
                equivalent=True,
            )
        ],
        freshness_checks=[
            FreshnessCheck(
                dimension="pilot-evidence",
                fresh=True,
                checked_at=checked_at,
                expires_at=checked_at + timedelta(days=1),
            )
        ],
        eligible_solution_count=1,
        converged=True,
    )
    return _seal(decision)


def _converged_case(state: OptimizationState) -> ConvergedCase:
    converged = ConvergedCase(
        **_base_envelope(state, "ConvergedCase"),
        origin=cast("Any", _origin(state)),
        request_digest=_digest_of(state, "OptimizationRequest"),
        source_snapshot_digest=_digest_of(state, "SourceSnapshot"),
        baseline_digest=_digest_of(state, "BaselineSnapshot"),
        evidence_bundle_digest=_digest_of(state, "EvidenceBundle"),
        solution_portfolio_digest=_digest_of(state, "SolutionPortfolio"),
        convergence_decision_digest=_digest_of(state, "ConvergenceDecision"),
    )
    return _seal(converged)


def _artifact_digest(payload: Mapping[str, Any] | BaseModel) -> str:
    data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    return sha256_digest(canonical_json(data))


__all__ = ["build_pilot_registrations", "build_pilot_runtime"]
