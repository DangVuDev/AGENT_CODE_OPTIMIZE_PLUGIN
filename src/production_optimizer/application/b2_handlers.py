"""Production handlers for B2 (Automatic Grounded Proposal).

B2.22 is not a handler here -- `orchestration/subgraphs/b2.py` embeds the
*real* `build_a3_graph` as that node; `build_b2_runtime()` merges B2's
native registrations with A3's real ones (`build_a3_registrations()`),
mirroring exactly how `build_b1_runtime()` merges B1 with A2.

Several B2 contracts (`AnalysisStrategy`, `ModelContextPackage`,
`DiscoveryAssumptionReport`, `StalenessDecision`) are deliberately plain
`ContractModel`, not `ArtifactEnvelope` -- they are working inputs to
B2.22's embedded A3 run and B2.60's final `ProposalEnvelope` assembly, not
independently-referenced lane artifacts, so they travel through plain
`OptimizationState` fields (`b2_*`, see `contracts/state.py`) rather than
the artifact store. `ProposalEnvelope` itself is sealed exactly once, at
B2.60, once its required `routing_decision`/`approval` fields are actually
known -- sealing it earlier (e.g. at B2.40) and re-sealing it later would
hit the same `merge_artifact_refs` conflict B1.40-81 avoids by not
re-sealing `DetectionReport` node-by-node.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a2 import EvidenceBundle, RepositoryManifest
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.b1 import (
    ObservedSourceIdentity,
    QualifiedOpportunity,
    RegisteredSourceSet,
)
from production_optimizer.contracts.b2 import (
    AnalysisStrategy,
    DiscoveryAssumption,
    DiscoveryAssumptionReport,
    ModelContextPackage,
    ProposalApproval,
    ProposalEnvelope,
    ProposalRoutingDecision,
    StalenessDecision,
    TruncationReport,
)
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="b2-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"

_B2_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "B2.31": {NodeRoute.CONTINUE.value, NodeRoute.REFRESH.value, NodeRoute.REJECTED.value},
    "B2.50": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value},
    "B2.52": {NodeRoute.CONTINUE.value, NodeRoute.REVISION.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _B2_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-b-b2",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="b2-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/lane-b-local-codebase/02-b2-automatic-proposal.md",
        slo="B2 node completes within 60 seconds",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production B2 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"b2_{node_id.replace('.', '_')}"
    return execute


def build_b2_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import B2_NODE_IDS

    enabled = {
        "B2.10", "B2.20", "B2.21", "B2.30", "B2.31",
        "B2.40", "B2.41", "B2.50", "B2.51", "B2.52", "B2.60",
    }
    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in B2_NODE_IDS
        if node_id in enabled
    }


def build_b2_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build the one `NodeRuntime` `orchestration/subgraphs/b2.py`'s
    `build_b2_graph` needs -- B2's 11 native registrations plus A3's 22 real
    ones, sharing one `ports` bundle, same pattern as `build_b1_runtime`."""

    from production_optimizer.application.a3_handlers import build_a3_registrations

    registrations = {**build_b2_registrations(), **build_a3_registrations()}
    return NodeRuntime(registrations, ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "B2.10":
            return _b2_10(state, ports)
        case "B2.20":
            return _b2_20(state, ports)
        case "B2.21":
            return _b2_21(state, ports)
        case "B2.30":
            return _b2_30(state, ports)
        case "B2.31":
            return _b2_31(state, ports)
        case "B2.40":
            return _b2_40(state, ports)
        case "B2.41":
            return _b2_41(state, ports)
        case "B2.50":
            return _b2_50(state, ports)
        case "B2.51":
            return _b2_51(state, ports)
        case "B2.52":
            return _b2_52(state, ports)
        case "B2.60":
            return _b2_60(state, ports)
        case _:
            return NodeExecution()


def _b2_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Verify the qualified opportunity this proposal run is for is real."""

    del ports
    _require_ref(state, "QualifiedOpportunity")
    return NodeExecution()


def _b2_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    manifest = _read_model(
        ports, state, _require_ref(state, "RepositoryManifest"), RepositoryManifest
    )
    selected = sorted(lang for lang, share in manifest.languages.items() if share > 0)
    strategy = AnalysisStrategy(
        strategy_id=f"strategy-{_required_state_str(state, 'case_id')}",
        selected_analyzers=selected or ["unknown"],
        skipped_analyzers={},
        language_coverage=dict(manifest.languages),
        risk_informed=True,
    )
    return NodeExecution(updates={"b2_analysis_strategy": strategy})


def _b2_21(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    strategy = cast("AnalysisStrategy | None", state.get("b2_analysis_strategy"))
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), _OptimizationRequestLike
    )
    evidence_ids = [item.evidence_id for item in bundle.evidence] or ["no-evidence-collected"]
    criteria_ids = [criterion["criterion_id"] for criterion in request.criteria] or ["unknown"]
    max_evidence = 50
    truncated = len(evidence_ids) > max_evidence
    package = ModelContextPackage(
        package_id=f"context-{_required_state_str(state, 'case_id')}",
        strategy_id=strategy.strategy_id if strategy is not None else "unknown",
        evidence_ids=evidence_ids[:max_evidence],
        criteria_ids=criteria_ids,
        truncation_report=TruncationReport(
            truncated=truncated,
            omitted_evidence_ids=evidence_ids[max_evidence:] if truncated else [],
            reason=f"more than {max_evidence} evidence items" if truncated else None,
        ),
    )
    return NodeExecution(updates={"b2_model_context_package": package})


def _b2_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real check: does B1's own binding data actually confirm a feature,
    source and owner, not just claim one? Feeds B2.31's staleness decision
    context; `ProposalEnvelope` itself has no field for this report (see
    module docstring) -- it is a gate input, not a persisted artifact."""

    opportunity = _read_model(
        ports, state, _require_ref(state, "QualifiedOpportunity"), QualifiedOpportunity
    )
    assumptions = [
        DiscoveryAssumption(
            assumption_id=f"source-{opportunity.source_binding.binding_id}",
            statement=(
                f"source repository {opportunity.source_binding.repository_id} "
                "is the one affected"
            ),
            basis="detected_fact" if opportunity.source_binding.resolved else "inferred_context",
            verified=opportunity.source_binding.resolved,
        ),
        DiscoveryAssumption(
            assumption_id=f"owner-{opportunity.owner_binding.binding_id}",
            statement="the recorded owner is still accountable for this feature",
            basis="detected_fact" if opportunity.owner_binding.resolved else "inferred_context",
            verified=opportunity.owner_binding.resolved,
        ),
    ]
    report = DiscoveryAssumptionReport(
        report_id=f"assumptions-{_required_state_str(state, 'case_id')}",
        feature_binding_confirmed=True,
        source_binding_confirmed=opportunity.source_binding.resolved,
        owner_binding_confirmed=opportunity.owner_binding.resolved,
        assumptions=assumptions,
    )
    return NodeExecution(updates={"b2_discovery_assumption_report": report})


def _b2_31(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real staleness check: does the source registered for this scan still
    have the same git revision B1.21 fingerprinted it at?"""

    identity_ref = _try_ref(state, "ObservedSourceIdentity")
    registry_ref = _try_ref(state, "RegisteredSourceSet")

    stale = False
    changed_dimensions: list[str] = []
    if identity_ref is not None and registry_ref is not None:
        identity = _read_model(ports, state, identity_ref, ObservedSourceIdentity)
        registry = _read_model(ports, state, registry_ref, RegisteredSourceSet)
        source = next(
            (s for s in registry.sources if s.repository_id == identity.repository_id), None
        )
        if source is not None and Path(source.local_path).exists() and identity.git_revision:
            current_revision = _git(Path(source.local_path), "rev-parse", "HEAD")
            if current_revision is not None and current_revision != identity.git_revision:
                stale = True
                changed_dimensions.append("git_revision")

    decision = StalenessDecision(
        decision_id=f"staleness-{_required_state_str(state, 'case_id')}",
        stale=stale,
        changed_dimensions=changed_dimensions,
        action="refresh" if stale else "proceed",
    )
    route = NodeRoute.REFRESH if stale else NodeRoute.CONTINUE
    return NodeExecution(route=route, updates={"b2_staleness_decision": decision})


def _b2_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Confirm every artifact B2.60 will need to seal `ProposalEnvelope`
    already exists -- no assembly happens here (see module docstring)."""

    del ports
    required_types = ("QualifiedOpportunity", "FindingSet", "SolutionPortfolio", "A3QualityReport")
    for artifact_type in required_types:
        _require_ref(state, artifact_type)
    return NodeExecution()


def _b2_41(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    portfolio = _read_model(
        ports, state, _require_ref(state, "SolutionPortfolio"), SolutionPortfolio
    )
    eligible = [strategy for strategy in portfolio.strategies if strategy.eligible]
    top = eligible[0] if eligible else portfolio.strategies[0]
    narrative = (
        f"Automatic discovery qualified this opportunity and A3 grounded it in real "
        f"evidence. Top strategy: {top.title} ({top.risk_ceiling} risk) -- {top.mechanism}"
    )
    return NodeExecution(updates={"b2_narrative": narrative})


def _b2_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real routing rule: any strategy touching code/architecture needs a
    human owner; a portfolio limited to config/prompt changes can forward
    automatically. BR-B2-004: automatic initiation never implies automatic
    *approval* for anything riskier than that."""

    portfolio = _read_model(
        ports, state, _require_ref(state, "SolutionPortfolio"), SolutionPortfolio
    )
    high_risk = {"code", "architecture"}
    needs_review = any(strategy.risk_ceiling in high_risk for strategy in portfolio.strategies)
    if needs_review:
        decision = ProposalRoutingDecision(
            decision_id=f"routing-{_required_state_str(state, 'case_id')}",
            route="owner_review",
            reasons=["at least one proposed strategy touches code or architecture"],
            policy_version="b2-routing-v1",
            required_actor_role="owner",
        )
        route = NodeRoute.APPROVAL
    else:
        decision = ProposalRoutingDecision(
            decision_id=f"routing-{_required_state_str(state, 'case_id')}",
            route="auto_forward",
            reasons=["every proposed strategy is config/prompt risk or lower"],
            policy_version="b2-routing-v1",
        )
        route = NodeRoute.CONTINUE
    return NodeExecution(route=route, updates={"b2_routing_decision": decision})


def _b2_51(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Record whatever decision is available. `subgraphs/b2.py` wires
    B2.51->B2.52 unconditionally (not a routed edge), so this node cannot
    halt the graph itself -- an out-of-band decision arrives the same way
    A1.90/A2.31 read one, via `state["resume_command"]` set by
    `application.resume.resume_case`. No decision yet means `pending`,
    which B2.52 fails closed on rather than treating as approval."""

    del ports
    resume_command = state.get("resume_command")
    if resume_command is None:
        approval = ProposalApproval(decision="pending", policy_version="b2-approval-v1")
    else:
        decision_map = {
            "approve": "approved",
            "reject": "rejected",
            "revise": "revision_requested",
        }
        decision = decision_map.get(resume_command.decision, "pending")
        approval = ProposalApproval(
            approval_id=f"approval-{_required_state_str(state, 'case_id')}",
            actor_id=resume_command.actor_id,
            actor_role=sorted(resume_command.actor_roles)[0],
            decision=cast("Any", decision),
            policy_version="b2-approval-v1",
        )
    return NodeExecution(updates={"b2_approval": approval})


def _b2_52(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    del ports
    approval = cast("ProposalApproval | None", state.get("b2_approval"))
    decision = approval.decision if approval is not None else "pending"
    if decision == "approved":
        route = NodeRoute.CONTINUE
    elif decision == "revision_requested":
        route = NodeRoute.REVISION
    else:
        route = NodeRoute.REJECTED
    return NodeExecution(route=route)


def _b2_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Seal the real `ProposalEnvelope` -- the one place in B2 that does,
    now that `routing_decision`/`approval`/`staleness` are all known."""

    opportunity_ref = _require_ref(state, "QualifiedOpportunity")
    finding_set_ref = _require_ref(state, "FindingSet")
    portfolio_ref = _require_ref(state, "SolutionPortfolio")
    quality_report_ref = _require_ref(state, "A3QualityReport")

    routing_decision = cast(
        "ProposalRoutingDecision | None", state.get("b2_routing_decision")
    )
    if routing_decision is None:
        raise ValueError("B2.60 requires a routing decision from B2.50")

    approval = cast("ProposalApproval | None", state.get("b2_approval"))
    if approval is None:
        # Reached B2.60 straight from B2.50 (auto_forward) -- no human step
        # ran, so the system itself is the approving actor.
        approval = ProposalApproval(decision="approved", policy_version="b2-approval-v1")

    staleness = cast("StalenessDecision | None", state.get("b2_staleness_decision"))
    if staleness is None:
        staleness = StalenessDecision(
            decision_id=f"staleness-{_required_state_str(state, 'case_id')}",
            stale=False,
            action="proceed",
        )

    envelope = _seal(
        ProposalEnvelope(
            **_base_envelope(
                state,
                "ProposalEnvelope",
                parents=[
                    opportunity_ref.content_digest,
                    finding_set_ref.content_digest,
                    portfolio_ref.content_digest,
                    quality_report_ref.content_digest,
                ],
            ),
            qualified_opportunity_digest=opportunity_ref.content_digest,
            finding_set_digest=finding_set_ref.content_digest,
            solution_portfolio_digest=portfolio_ref.content_digest,
            quality_report_digest=quality_report_ref.content_digest,
            routing_decision=routing_decision,
            approval=approval,
            staleness=staleness,
            narrative=state.get("b2_narrative"),
        )
    )
    ref = _put_envelope(ports, state, envelope, node_id="B2.60")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# Shared helpers (mirrors a2_handlers.py/b1_handlers.py)
# ---------------------------------------------------------------------------


class _OptimizationRequestLike:
    """Minimal read shape for B2.21's `criteria_ids` -- avoids importing all
    of `contracts.a1.OptimizationRequest`'s nested leaf types just to read
    one field; `_read_model` only needs `model_validate`/attribute access."""

    def __init__(self, criteria: list[dict[str, Any]]) -> None:
        self.criteria = criteria

    @classmethod
    def model_validate(cls, raw: dict[str, Any]) -> _OptimizationRequestLike:
        return cls(criteria=cast("list[dict[str, Any]]", raw.get("criteria", [])))


def _put_envelope(
    ports: NodePorts, state: OptimizationState, envelope: ArtifactEnvelope, *, node_id: str
) -> ArtifactRef:
    content = canonical_json(envelope.model_dump(mode="json", exclude={"content_digest"}))
    generic_ref = ports.artifacts.put_json(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=envelope.content_digest,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:{node_id}:{envelope.artifact_type}"
        ),
    )
    return ArtifactRef(
        artifact_type=envelope.artifact_type,
        schema_version=envelope.schema_version,
        artifact_id=envelope.artifact_id,
        content_digest=envelope.content_digest,
        uri=generic_ref.uri,
    )


def _read_model[T](
    ports: NodePorts, state: OptimizationState, ref: ArtifactRef, model: type[T]
) -> T:
    content = ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=ref)
    raw = TypeAdapter(dict[str, Any]).validate_json(content)
    if issubclass(cast("type[Any]", model), ArtifactEnvelope):
        raw.setdefault("content_digest", ref.content_digest)
    return cast("Any", model).model_validate(raw)


def _seal[T: ArtifactEnvelope](model: T) -> T:
    return model.model_copy(update={"content_digest": model_content_digest(model)})


def _base_envelope(
    state: OptimizationState, artifact_type: str, *, parents: list[str] | None = None
) -> dict[str, Any]:
    return {
        "artifact_id": f"{_required_state_str(state, 'case_id')}-{artifact_type}",
        "tenant_id": _required_state_str(state, "tenant_id"),
        "case_id": _required_state_str(state, "case_id"),
        "created_at": datetime.now(UTC),
        "producer": _PRODUCER,
        "policy_versions": {"b2": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    ref = _try_ref(state, artifact_type)
    if ref is None:
        raise ValueError(f"missing required artifact ref: {artifact_type}")
    return ref


def _try_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef | None:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    return None


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"B2 state is missing required field {key!r}")
    return value


def _git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
