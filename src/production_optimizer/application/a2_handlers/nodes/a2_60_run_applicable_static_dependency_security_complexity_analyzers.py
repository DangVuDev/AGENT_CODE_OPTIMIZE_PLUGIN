# pyright: reportPrivateUsage=false
"""Implementation of business node A2.60."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _run_command_evidence_branch,
)


def handle_a2_60_run_applicable_static_dependency_security_complexity_analyzers(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    return _run_command_evidence_branch(
        state, ports, node_id="A2.60", branch_kind="static", command_kinds=("lint", "type")
    )


__all__ = ["handle_a2_60_run_applicable_static_dependency_security_complexity_analyzers"]
