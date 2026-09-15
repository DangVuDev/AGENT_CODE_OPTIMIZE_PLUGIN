# pyright: reportPrivateUsage=false
"""Implementation of business node S01.30."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.state import OptimizationState

from ..shared import _EFFORT_SCALE, _REVERSIBILITY_SCALE, _read_required


def handle_s01_30_normalize_benefit_effort_reversibility_and_risk_to_unit_scale(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Put benefit, effort, reversibility and risk on a real 0-1 scale per
    eligible strategy; raw forecasts stay on the strategy itself, this only
    normalizes them for scoring (BR-01-003 keeps the components inspectable)."""

    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    eligible_ids = set(cast("list[str]", state.get("s01_eligible_strategy_ids") or []))
    normalized: dict[str, dict[str, Any]] = {}
    for strategy in portfolio.strategies:
        if strategy.strategy_id not in eligible_ids:
            continue
        benefit_by_criterion = {
            impact.criterion_id: impact.confidence
            for impact in strategy.impact_assessment.criterion_impacts
            if impact.direction == "improves"
        }
        normalized[strategy.strategy_id] = {
            "benefit_by_criterion": benefit_by_criterion,
            "effort_score": _EFFORT_SCALE[strategy.tradeoff_analysis.effort],
            "reversibility_score": _REVERSIBILITY_SCALE[strategy.risk_assessment.reversibility],
            "risk_penalty": strategy.risk_assessment.uncertainty,
        }
    return NodeExecution(updates={"s01_normalized_factors": normalized})


__all__ = ["handle_s01_30_normalize_benefit_effort_reversibility_and_risk_to_unit_scale"]
