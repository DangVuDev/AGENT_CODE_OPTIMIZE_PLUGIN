# pyright: reportPrivateUsage=false
"""Implementation of business node B1.62."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import DetectionSignal, OwnershipBinding
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _required_state_str,
)


def handle_b1_62_resolve_code_service_decision_owners_detect_disagreement(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    if not signals:
        return NodeExecution(updates={"b1_ownership_bindings": []})
    binding = OwnershipBinding(
        binding_id=f"owner-binding-{_required_state_str(state, 'case_id')}",
        code_owner=None,
        service_owner=None,
        decision_owner=None,
        conflicts=[],
        resolved=False,
    )
    return NodeExecution(updates={"b1_ownership_bindings": [binding]})


__all__ = ["handle_b1_62_resolve_code_service_decision_owners_detect_disagreement"]
