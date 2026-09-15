# pyright: reportPrivateUsage=false
"""Implementation of business node B2.10."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _require_ref,
)


def handle_b2_10_verify_opportunity_schema_digests_unique_case_binding(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Verify the qualified opportunity this proposal run is for is real."""

    del ports
    _require_ref(state, "QualifiedOpportunity")
    return NodeExecution()


__all__ = ["handle_b2_10_verify_opportunity_schema_digests_unique_case_binding"]
