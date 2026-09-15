# pyright: reportPrivateUsage=false
"""Named production entry point for business node B1.52."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import _detector, _eligible_run_groups


def handle_b1_52_aggregate_repeated_slow_spans_profile_frames_error(
    state: OptimizationState, ports: NodePorts | None, /
) -> NodeExecution:
    """Execute the B1.52 business responsibility."""

    del ports
    return _detector(state, run_groups=_eligible_run_groups(state))


__all__ = ["handle_b1_52_aggregate_repeated_slow_spans_profile_frames_error"]
