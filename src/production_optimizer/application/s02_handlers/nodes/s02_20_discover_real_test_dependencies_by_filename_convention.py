# pyright: reportPrivateUsage=false
"""Implementation of business node S02.20."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import RepositoryManifest, SourceSnapshot
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.state import OptimizationState

from ..shared import _read_required, _read_selected_solution, _strategy_by_id


def handle_s02_20_discover_real_test_dependencies_by_filename_convention(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real dependency discovery: match each scope file to a real test file
    under the repository's own detected test roots by filename convention."""

    selected = _read_selected_solution(ports, state)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    manifest = cast("RepositoryManifest", _read_required(ports, state, "RepositoryManifest"))
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    root = Path(snapshot.canonical_path_ref)

    dependency_map: dict[str, list[str]] = {}
    for entry in strategy.scope_resolution.entries:
        if entry.kind != "file":
            continue
        stem = Path(entry.path_or_symbol).stem
        matches: list[str] = []
        for test_root in manifest.test_roots:
            test_dir = root / test_root
            if not test_dir.exists():
                continue
            matches.extend(
                str(candidate.relative_to(root))
                for candidate in test_dir.rglob(f"*{stem}*")
                if candidate.is_file()
            )
        dependency_map[entry.path_or_symbol] = sorted(matches)
    return NodeExecution(updates={"s02_dependency_map": dependency_map})


__all__ = ["handle_s02_20_discover_real_test_dependencies_by_filename_convention"]
