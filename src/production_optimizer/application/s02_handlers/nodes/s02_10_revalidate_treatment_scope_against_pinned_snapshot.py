# pyright: reportPrivateUsage=false
"""Implementation of business node S02.10."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.state import OptimizationState

from ..shared import _read_required, _read_selected_solution, _strategy_by_id


def handle_s02_10_revalidate_treatment_scope_against_pinned_snapshot(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Revalidate the selected treatment's scope against the pinned
    snapshot -- source may have drifted since A3/S01 ran."""

    selected = _read_selected_solution(ports, state)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    strategy = _strategy_by_id(portfolio, selected.strategy_id)

    root = Path(snapshot.canonical_path_ref)
    still_resolved = all(
        entry.proposed_creation or (root / entry.path_or_symbol).exists()
        for entry in strategy.scope_resolution.entries
    )
    route = NodeRoute.CONTINUE if still_resolved else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"s02_resolution_ok": still_resolved})


__all__ = ["handle_s02_10_revalidate_treatment_scope_against_pinned_snapshot"]
