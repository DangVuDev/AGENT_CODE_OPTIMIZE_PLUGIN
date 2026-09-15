# pyright: reportPrivateUsage=false
"""Implementation of business node S01.10."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import _POLICY_VERSION, _require_ref


def handle_s01_10_freeze_decision_context_pin_portfolio_and_request_digests(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Freeze decision context: pin the portfolio/request digests, policy
    version and any prior-REVERT exclusion list this run must honor."""

    del ports
    portfolio_ref = _require_ref(state, "SolutionPortfolio")
    request_ref = _require_ref(state, "OptimizationRequest")
    _require_ref(state, "ConvergedCase")
    excluded = cast("list[str]", state.get("s01_excluded_strategy_ids") or [])
    context = {
        "solution_portfolio_digest": portfolio_ref.content_digest,
        "request_digest": request_ref.content_digest,
        "excluded_strategy_ids": excluded,
        "policy_version": _POLICY_VERSION,
    }
    return NodeExecution(updates={"s01_decision_context": context})


__all__ = ["handle_s01_10_freeze_decision_context_pin_portfolio_and_request_digests"]
