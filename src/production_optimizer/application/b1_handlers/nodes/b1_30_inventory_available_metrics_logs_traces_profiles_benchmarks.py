# pyright: reportPrivateUsage=false
"""Implementation of business node B1.30."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import (
    HistoricalRevision,
    HistoricalSourceInventory,
    RegisteredSourceSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _git,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_b1_30_inventory_available_metrics_logs_traces_profiles_benchmarks(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    registry = _read_model(
        ports, state, _require_ref(state, "RegisteredSourceSet"), RegisteredSourceSet
    )
    source = registry.sources[0] if registry.sources else None

    revisions: list[HistoricalRevision] = []
    source_id = "none"
    if source is not None:
        source_id = source.source_id
        root = Path(source.local_path)
        if root.exists():
            git_root = _git(root, "rev-parse", "--show-toplevel")
            exact_git_root = bool(git_root) and Path(git_root).resolve() == root.resolve()
            log = _git(root, "log", "-n", "20", "--format=%H %cI") if exact_git_root else ""
            for line in log.splitlines() if log else []:
                parts = line.strip().split(" ", 1)
                if len(parts) != 2:
                    continue
                revision_id, iso_ts = parts
                try:
                    observed_at = datetime.fromisoformat(iso_ts)
                except ValueError:
                    continue
                revisions.append(
                    HistoricalRevision(revision_id=revision_id, observed_at=observed_at)
                )

    inventory = _seal(
        HistoricalSourceInventory(
            **_base_envelope(state, "HistoricalSourceInventory"),
            source_id=source_id,
            revisions=revisions,
        )
    )
    ref = _put_envelope(ports, state, inventory, node_id="B1.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_b1_30_inventory_available_metrics_logs_traces_profiles_benchmarks"]
