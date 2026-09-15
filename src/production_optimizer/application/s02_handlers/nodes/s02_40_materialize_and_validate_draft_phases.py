# pyright: reportPrivateUsage=false
"""Implementation of business node S02.40."""

from __future__ import annotations

from typing import Any, cast

from pydantic import ValidationError

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.s02 import ExecutionPhase
from production_optimizer.contracts.state import OptimizationState


def handle_s02_40_materialize_and_validate_draft_phases(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Materialize phases: real Pydantic validation of the draft, not a
    feasibility check (feasibility is S02.81's job)."""

    del ports
    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    phases: list[ExecutionPhase] = []
    failures: list[str] = []
    for raw in draft.get("phases", []):
        try:
            phases.append(ExecutionPhase.model_validate(raw))
        except ValidationError as exc:
            failures.append(f"{raw.get('phase_id', '<unknown>')}: {exc}")
    updated = {**draft, "phases": phases, "phase_failures": failures}
    return NodeExecution(updates={"s02_plan_draft": updated})


__all__ = ["handle_s02_40_materialize_and_validate_draft_phases"]
