# pyright: reportPrivateUsage=false
"""Implementation of business node A2.62."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_model,
    _require_ref,
    _run_command_evidence_branch,
    _run_compose_evidence_branch,
)


def handle_a2_62_run_exact_warmups_repetitions_preserve_each_sample(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    if request.execution is not None:
        return _run_compose_evidence_branch(state, ports, request=request)
    return _run_command_evidence_branch(
        state, ports, node_id="A2.62", branch_kind="metric", command_kinds=("benchmark",)
    )


__all__ = ["handle_a2_62_run_exact_warmups_repetitions_preserve_each_sample"]
