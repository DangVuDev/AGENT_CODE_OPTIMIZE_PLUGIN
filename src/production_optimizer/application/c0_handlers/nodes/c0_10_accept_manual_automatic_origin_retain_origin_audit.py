# pyright: reportPrivateUsage=false
"""Implementation of business node C0.10."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _origin,
    _read_required,
)


def handle_c0_10_accept_manual_automatic_origin_retain_origin_audit(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """BR-C0-001: a lane origin cannot change after case creation. The only
    two independent sources of "what origin is this case" are `state["lane"]`
    (set once at case creation) and the already-sealed `OptimizationRequest`
    (sealed by A1.95 for Lane A, or reconstructed by B1.90 for Lane B) --
    real disagreement between them means the request was tampered with or a
    real bug ran, so this fails hard rather than routing a soft rejection."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    expected_origin = _origin(state)
    if request.origin.value != expected_origin:
        raise ValueError(
            f"BR-C0-001 violation: case lane is {expected_origin!r} but the sealed "
            f"OptimizationRequest.origin is {request.origin.value!r}"
        )
    return NodeExecution()


__all__ = ["handle_c0_10_accept_manual_automatic_origin_retain_origin_audit"]
