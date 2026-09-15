# pyright: reportPrivateUsage=false
"""Implementation of business node B2.50."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.b2 import ProposalRoutingDecision
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_model,
    _require_ref,
    _required_state_str,
)


def handle_b2_50_route_risk_security_confidence_ownership_policy(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
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


__all__ = ["handle_b2_50_route_risk_security_confidence_ownership_policy"]
