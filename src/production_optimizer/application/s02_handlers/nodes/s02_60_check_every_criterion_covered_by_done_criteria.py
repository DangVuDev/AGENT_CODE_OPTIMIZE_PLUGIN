# pyright: reportPrivateUsage=false
"""Implementation of business node S02.60."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.s02 import ExecutionPhase
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _criterion_coverage,
    _read_optional,
    _read_required,
    _read_selected_solution,
    _strategy_by_id,
)


def handle_s02_60_check_every_criterion_covered_by_done_criteria(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Define done criteria: real coverage check -- every criterion the
    selected strategy claims to affect must be named in at least one
    phase's `done_criteria`."""

    selected = _read_selected_solution(ports, state)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    request = cast(
        "OptimizationRequest | None", _read_optional(ports, state, "OptimizationRequest")
    )
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    phases = cast("list[ExecutionPhase]", draft.get("phases") or [])
    coverage = _criterion_coverage(phases, strategy, request)
    updated = {**draft, "acceptance_coverage": coverage}
    return NodeExecution(updates={"s02_plan_draft": updated})


__all__ = ["handle_s02_60_check_every_criterion_covered_by_done_criteria"]
