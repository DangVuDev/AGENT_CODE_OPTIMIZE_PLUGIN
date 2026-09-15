# pyright: reportPrivateUsage=false
"""Implementation of business node B1.71."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.b1 import (
    OpportunityScore,
    OwnershipBinding,
    QualificationDecision,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _QUALIFICATION_THRESHOLD,
    _required_state_str,
)


def handle_b1_71_apply_hard_trust_identity_samples_actionability_policy(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    del ports
    scores = cast("list[OpportunityScore]", state.get("b1_scores", []))
    ownership_bindings = cast("list[OwnershipBinding]", state.get("b1_ownership_bindings", []))

    if not scores:
        decision = QualificationDecision(
            decision_id=f"qualification-{_required_state_str(state, 'case_id')}",
            qualified=False,
            reasons=["no detection signal met eligibility"],
            policy_version="b1-qualification-v1",
        )
        return NodeExecution(
            route=NodeRoute.REJECTED, updates={"b1_qualification_decisions": [decision]}
        )

    top = max(scores, key=lambda score: score.composite)
    qualified = top.composite >= _QUALIFICATION_THRESHOLD
    decision = QualificationDecision(
        decision_id=f"qualification-{_required_state_str(state, 'case_id')}",
        qualified=qualified,
        reasons=[] if qualified else [f"composite score {top.composite} below threshold"],
        policy_version="b1-qualification-v1",
    )
    if qualified:
        route = NodeRoute.CONTINUE
    elif any(not binding.resolved and binding.conflicts for binding in ownership_bindings):
        route = NodeRoute.QUARANTINE
    else:
        route = NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"b1_qualification_decisions": [decision]})


__all__ = ["handle_b1_71_apply_hard_trust_identity_samples_actionability_policy"]
