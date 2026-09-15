# pyright: reportPrivateUsage=false
"""Implementation of business node B2.52."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.b2 import ProposalApproval
from production_optimizer.contracts.state import OptimizationState


def handle_b2_52_apply_rejection_cooldown_target_requested_a3_b2(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
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


__all__ = ["handle_b2_52_apply_rejection_cooldown_target_requested_a3_b2"]
