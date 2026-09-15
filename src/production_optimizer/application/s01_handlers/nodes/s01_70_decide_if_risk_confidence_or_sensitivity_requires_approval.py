# pyright: reportPrivateUsage=false
"""Implementation of business node S01.70."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.state import OptimizationState

from ..shared import _APPROVAL_RISK_TIERS, _LOW_CONFIDENCE_THRESHOLD, _read_required


def handle_s01_70_decide_if_risk_confidence_or_sensitivity_requires_approval(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """BR-01-004: high-risk, low-confidence and close-call selections require
    human approval; auto-select only below configured risk/uncertainty."""

    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    ranked_ids = cast("list[str]", state.get("s01_ranked_strategy_ids") or [])
    sensitivity_flags = cast("list[str]", state.get("s01_sensitivity_flags") or [])
    winner_id = ranked_ids[0]
    winner = next(s for s in portfolio.strategies if s.strategy_id == winner_id)

    reasons: list[str] = []
    if winner.risk_ceiling in _APPROVAL_RISK_TIERS:
        reasons.append(f"winning strategy risk_ceiling={winner.risk_ceiling!r}")
    if sensitivity_flags:
        reasons.append("ranking is sensitive to reasonable weight changes")

    improving_impacts = [
        impact
        for impact in winner.impact_assessment.criterion_impacts
        if impact.direction == "improves"
    ]
    if not improving_impacts:
        reasons.append("winning strategy has no criterion impact marked 'improves'")
    else:
        avg_confidence = sum(impact.confidence for impact in improving_impacts) / len(
            improving_impacts
        )
        if avg_confidence < _LOW_CONFIDENCE_THRESHOLD:
            reasons.append(
                f"winning strategy average impact confidence {avg_confidence:.2f} is "
                f"below {_LOW_CONFIDENCE_THRESHOLD}"
            )

    decision = {
        "needs_approval": bool(reasons),
        "reasons": reasons,
        "winner_strategy_id": winner_id,
    }
    return NodeExecution(updates={"s01_routing_decision": decision})


__all__ = ["handle_s01_70_decide_if_risk_confidence_or_sensitivity_requires_approval"]
