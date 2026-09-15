# pyright: reportPrivateUsage=false
"""Implementation of business node A1.95."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _optimization_request,
    _put_envelope,
)


def handle_a1_95_canonicalize_calculate_digest_sign_metadata_create_persist(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    request = _optimization_request(state, ports)
    ref = _put_envelope(ports, state, request, node_id="A1.95")
    return NodeExecution(updates={"request_ref": ref, "artifact_refs": [ref]})


__all__ = ["handle_a1_95_canonicalize_calculate_digest_sign_metadata_create_persist"]
