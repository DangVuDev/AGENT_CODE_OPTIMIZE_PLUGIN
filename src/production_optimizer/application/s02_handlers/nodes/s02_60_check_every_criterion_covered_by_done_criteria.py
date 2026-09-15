# pyright: reportPrivateUsage=false
"""Implementation of business node S02.60."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.s02 import ExecutionPhase
from production_optimizer.contracts.state import OptimizationState

from ..shared import _read_required, _read_selected_solution, _strategy_by_id


def handle_s02_60_check_every_criterion_covered_by_done_criteria(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Define done criteria: real coverage check -- every criterion the
    selected strategy claims to affect must be named in at least one
    phase's `done_criteria`."""

    selected = _read_selected_solution(ports, state)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    phases = cast("list[ExecutionPhase]", draft.get("phases") or [])
    all_criteria_text = " ".join(
        criterion for phase in phases for criterion in phase.done_criteria
    ).lower()
    coverage = {
        impact.criterion_id: impact.criterion_id.lower() in all_criteria_text
        for impact in strategy.impact_assessment.criterion_impacts
    }
    updated = {**draft, "acceptance_coverage": coverage}
    return NodeExecution(updates={"s02_plan_draft": updated})


__all__ = ["handle_s02_60_check_every_criterion_covered_by_done_criteria"]
