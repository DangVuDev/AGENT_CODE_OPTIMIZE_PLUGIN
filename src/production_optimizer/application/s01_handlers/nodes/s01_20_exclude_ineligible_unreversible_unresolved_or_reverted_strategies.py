# pyright: reportPrivateUsage=false
"""Implementation of business node S01.20."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.state import OptimizationState

from ..shared import _read_required


def handle_s01_20_exclude_ineligible_unreversible_unresolved_or_reverted_strategies(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """BR-01-001: ineligible strategies can never be approved. Excludes A3's
    own ineligible strategies, plus any strategy whose rollback plan is not
    reversible or whose scope was never fully resolved, plus any strategy a
    prior Step 06 REVERT already excluded."""

    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    excluded = set(cast("list[str]", state.get("s01_excluded_strategy_ids") or []))
    eligible_ids = [
        strategy.strategy_id
        for strategy in portfolio.strategies
        if strategy.eligible
        and strategy.rollback_plan.reversible
        and strategy.scope_resolution.fully_resolved
        and strategy.strategy_id not in excluded
    ]
    route = NodeRoute.CONTINUE if eligible_ids else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"s01_eligible_strategy_ids": eligible_ids})


__all__ = ["handle_s01_20_exclude_ineligible_unreversible_unresolved_or_reverted_strategies"]
