# pyright: reportPrivateUsage=false
"""Implementation of business node S01.50."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.s01 import StrategyScore
from production_optimizer.contracts.state import OptimizationState

from ..shared import _rank


def handle_s01_50_rank_by_total_score_with_risk_tier_tiebreak(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Risk ladder: rank by `total_score`, but when strategies are within a
    material tie margin, prefer the lower risk tier (experiment_config <
    prompt < code < architecture) -- applicability outranks artificial tier
    diversity, so this only breaks genuine ties, never overrides a real
    score gap."""

    del ports
    scores = cast("list[StrategyScore]", state.get("s01_scores") or [])
    return NodeExecution(updates={"s01_ranked_strategy_ids": _rank(scores)})


__all__ = ["handle_s01_50_rank_by_total_score_with_risk_tier_tiebreak"]
