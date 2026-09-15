# pyright: reportPrivateUsage=false
"""Implementation of business node B2.41."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_model,
    _require_ref,
)


def handle_b2_41_produce_concise_explanation_sealed_structured_facts(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    portfolio = _read_model(
        ports, state, _require_ref(state, "SolutionPortfolio"), SolutionPortfolio
    )
    eligible = [strategy for strategy in portfolio.strategies if strategy.eligible]
    top = eligible[0] if eligible else portfolio.strategies[0]
    narrative = (
        f"Automatic discovery qualified this opportunity and A3 grounded it in real "
        f"evidence. Top strategy: {top.title} ({top.risk_ceiling} risk) -- {top.mechanism}"
    )
    return NodeExecution(updates={"b2_narrative": narrative})


__all__ = ["handle_b2_41_produce_concise_explanation_sealed_structured_facts"]
