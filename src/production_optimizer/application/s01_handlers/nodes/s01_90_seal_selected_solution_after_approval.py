# pyright: reportPrivateUsage=false
"""Implementation of business node S01.90."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.s01 import SelectedSolution, SelectionApproval
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _pass_stage_id,
    _put_envelope,
    _require_ref,
    _require_stage_ref,
    _s01_pass_number,
    _seal,
    _stage_envelope,
)


def handle_s01_90_seal_selected_solution_after_approval(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Seal `SelectedSolution` -- only reached once S01.80 secured approval.

    Both reads and the seal below are pass-scoped (see the S01.80 node's
    docstring): after a real REVERT round-trip, `state["artifact_refs"]`
    holds the prior pass's `RankingResult`/`SelectedSolution` alongside this
    pass's -- a plain first-match-by-type lookup would silently pick
    whichever sorts first (the stale one), not necessarily this pass's.
    `_require_stage_ref` pins the exact artifact this same pass's S01.80 just
    sealed, and the `SelectedSolution` seal below uses the same pass-scoped
    `artifact_id` for the identical `merge_artifact_refs` reason.
    """

    pass_number = _s01_pass_number(state)
    ranking_ref = _require_stage_ref(state, _pass_stage_id("S01.80", pass_number), "RankingResult")
    portfolio_ref = _require_ref(state, "SolutionPortfolio")
    routing = cast("dict[str, Any]", state.get("s01_routing_decision") or {})
    approval = cast("SelectionApproval | None", state.get("s01_approval"))
    if approval is None:
        raise ValueError("S01.90 requires an approval decision from S01.80")
    excluded = cast("list[str]", state.get("s01_excluded_strategy_ids") or [])

    selected = _seal(
        SelectedSolution(
            **_stage_envelope(
                state, _pass_stage_id("S01.90", pass_number), "SelectedSolution", parents=[]
            ),
            ranking_result_digest=ranking_ref.content_digest,
            solution_portfolio_digest=portfolio_ref.content_digest,
            strategy_id=cast("str", routing["winner_strategy_id"]),
            approval=approval,
            excluded_strategy_ids=excluded,
        )
    )
    ref = _put_envelope(ports, state, selected, node_id="S01.90")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_s01_90_seal_selected_solution_after_approval"]
