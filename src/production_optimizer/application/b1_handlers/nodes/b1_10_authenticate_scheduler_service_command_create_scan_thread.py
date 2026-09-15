# pyright: reportPrivateUsage=false
"""Implementation of business node B1.10."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import DiscoveryScanContext
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _required_state_str,
    _seal,
)


def handle_b1_10_authenticate_scheduler_service_command_create_scan_thread(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    now = datetime.now(UTC)
    context = _seal(
        DiscoveryScanContext(
            **_base_envelope(state, "DiscoveryScanContext"),
            scan_id=_required_state_str(state, "case_id"),
            window_start=now - timedelta(hours=24),
            window_end=now,
            trigger="scheduled",
        )
    )
    ref = _put_envelope(ports, state, context, node_id="B1.10")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_b1_10_authenticate_scheduler_service_command_create_scan_thread"]
