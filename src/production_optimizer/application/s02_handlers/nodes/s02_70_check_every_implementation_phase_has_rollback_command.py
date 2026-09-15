# pyright: reportPrivateUsage=false
"""Implementation of business node S02.70."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.s02 import ExecutionPhase
from production_optimizer.contracts.state import OptimizationState


def handle_s02_70_check_every_implementation_phase_has_rollback_command(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Define rollback: real check -- every implementation-kind phase must
    carry a real `rollback_command`, not just a trigger description."""

    del ports
    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    phases = cast("list[ExecutionPhase]", draft.get("phases") or [])
    reasons = [
        f"{phase.phase_id}: implementation phase has no rollback_command"
        for phase in phases
        if phase.phase_kind == "implementation" and phase.rollback_command is None
    ]
    updated = {**draft, "rollback_ok": not reasons, "rollback_reasons": reasons}
    return NodeExecution(updates={"s02_plan_draft": updated})


__all__ = ["handle_s02_70_check_every_implementation_phase_has_rollback_command"]
