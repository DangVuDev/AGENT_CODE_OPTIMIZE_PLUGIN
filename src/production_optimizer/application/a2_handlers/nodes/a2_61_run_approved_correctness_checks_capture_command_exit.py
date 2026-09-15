# pyright: reportPrivateUsage=false
"""Implementation of business node A2.61."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _run_command_evidence_branch,
)


def handle_a2_61_run_approved_correctness_checks_capture_command_exit(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    return _run_command_evidence_branch(
        state, ports, node_id="A2.61", branch_kind="test", command_kinds=("unit",)
    )


__all__ = ["handle_a2_61_run_approved_correctness_checks_capture_command_exit"]
