# pyright: reportPrivateUsage=false
"""Implementation of business node S01.60."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.s01 import StrategyScore
from production_optimizer.contracts.state import OptimizationState

from ..shared import _SENSITIVITY_PERTURBATION, _perturb_score, _rank, _read_required


def handle_s01_60_sensitivity_sweep_flags_close_calls_on_weight_change(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real sensitivity sweep: for each criterion, boost its weight by 50%
    and recompute the ranking; flag any criterion whose reasonable weight
    change would flip the top pick -- feeds S01.70's close-call detection."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    scores = cast("list[StrategyScore]", state.get("s01_scores") or [])
    ranked_ids = cast("list[str]", state.get("s01_ranked_strategy_ids") or [])
    if not ranked_ids:
        return NodeExecution(updates={"s01_sensitivity_flags": []})
    current_winner = ranked_ids[0]

    flags: list[str] = []
    for criterion in request.criteria:
        perturbed = [
            _perturb_score(score, criterion.criterion_id, _SENSITIVITY_PERTURBATION)
            for score in scores
        ]
        perturbed_ranking = _rank(perturbed)
        if perturbed_ranking and perturbed_ranking[0] != current_winner:
            flags.append(f"top choice changes if {criterion.criterion_id!r} weight increases 50%")
    return NodeExecution(updates={"s01_sensitivity_flags": flags})


__all__ = ["handle_s01_60_sensitivity_sweep_flags_close_calls_on_weight_change"]
