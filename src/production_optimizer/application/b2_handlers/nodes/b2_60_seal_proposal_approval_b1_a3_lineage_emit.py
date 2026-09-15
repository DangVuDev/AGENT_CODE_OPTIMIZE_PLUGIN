# pyright: reportPrivateUsage=false
"""Implementation of business node B2.60."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b2 import (
    ProposalApproval,
    ProposalEnvelope,
    ProposalRoutingDecision,
    StalenessDecision,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_b2_60_seal_proposal_approval_b1_a3_lineage_emit(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Seal the real `ProposalEnvelope` -- the one place in B2 that does,
    now that `routing_decision`/`approval`/`staleness` are all known."""

    opportunity_ref = _require_ref(state, "QualifiedOpportunity")
    finding_set_ref = _require_ref(state, "FindingSet")
    portfolio_ref = _require_ref(state, "SolutionPortfolio")
    quality_report_ref = _require_ref(state, "A3QualityReport")

    routing_decision = cast("ProposalRoutingDecision | None", state.get("b2_routing_decision"))
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


__all__ = ["handle_b2_60_seal_proposal_approval_b1_a3_lineage_emit"]
