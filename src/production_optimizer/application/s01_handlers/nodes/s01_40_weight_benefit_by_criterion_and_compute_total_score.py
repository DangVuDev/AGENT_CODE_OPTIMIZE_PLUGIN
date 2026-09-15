# pyright: reportPrivateUsage=false
"""Implementation of business node S01.40."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.s01 import StrategyScore
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _BENEFIT_WEIGHT,
    _EFFORT_WEIGHT,
    _REVERSIBILITY_WEIGHT,
    _RISK_WEIGHT,
    _read_required,
)


def handle_s01_40_weight_benefit_by_criterion_and_compute_total_score(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Weight each normalized benefit by the request's own criterion weight
    (normalized so weights sum to 1, since `Criterion.weight` is only
    constrained `> 0`, not pre-normalized) and combine with effort,
    reversibility and risk into `total_score`."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    strategies_by_id = {strategy.strategy_id: strategy for strategy in portfolio.strategies}
    factors = cast("dict[str, dict[str, Any]]", state.get("s01_normalized_factors") or {})
    total_weight = sum(criterion.weight for criterion in request.criteria) or 1.0
    weight_by_criterion = {
        criterion.criterion_id: criterion.weight / total_weight for criterion in request.criteria
    }

    scores: list[StrategyScore] = []
    for strategy_id, factor in factors.items():
        strategy = strategies_by_id[strategy_id]
        benefit_by_criterion = cast("dict[str, float]", factor["benefit_by_criterion"])
        criterion_scores = {
            criterion_id: benefit * weight_by_criterion.get(criterion_id, 0.0)
            for criterion_id, benefit in benefit_by_criterion.items()
        }
        effort_score = cast("float", factor["effort_score"])
        reversibility_score = cast("float", factor["reversibility_score"])
        risk_penalty = cast("float", factor["risk_penalty"])
        total = (
            _BENEFIT_WEIGHT * sum(criterion_scores.values())
            + _EFFORT_WEIGHT * effort_score
            + _REVERSIBILITY_WEIGHT * reversibility_score
            - _RISK_WEIGHT * risk_penalty
        )
        scores.append(
            StrategyScore(
                strategy_id=strategy_id,
                risk_tier=cast("Any", strategy.risk_ceiling),
                criterion_scores=criterion_scores,
                effort_score=effort_score,
                reversibility_score=reversibility_score,
                risk_penalty=risk_penalty,
                total_score=total,
            )
        )
    return NodeExecution(updates={"s01_scores": scores})


__all__ = ["handle_s01_40_weight_benefit_by_criterion_and_compute_total_score"]
