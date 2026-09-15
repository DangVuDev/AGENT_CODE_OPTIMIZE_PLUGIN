# pyright: reportPrivateUsage=false
"""Implementation of business node B1.91."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.platform import PolicyRequest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_model,
    _require_ref,
    _required_state_str,
)


def handle_b1_91_apply_automatic_intake_policy_request_owner_confirmation(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    if ports.policy is None:
        raise RuntimeError("B1.91 requires ports.policy to be non-None")
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    decision = ports.policy.evaluate(
        PolicyRequest(
            decision_type="automatic_intake",
            policy_version="b1-intake-v1",
            tenant_id=_required_state_str(state, "tenant_id"),
            facts={"feature_id": request.objective.feature_id},
        )
    )
    if decision.allowed:
        route = NodeRoute.CONTINUE
    elif decision.decision == "deny":
        route = NodeRoute.REJECTED
    else:
        route = NodeRoute.APPROVAL
    return NodeExecution(route=route)


__all__ = ["handle_b1_91_apply_automatic_intake_policy_request_owner_confirmation"]
