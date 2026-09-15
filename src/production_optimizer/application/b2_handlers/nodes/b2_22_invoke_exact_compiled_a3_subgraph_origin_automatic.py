"""Named production entry point for business node B2.22."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState


def handle_b2_22_invoke_exact_compiled_a3_subgraph_origin_automatic(
    state: OptimizationState, ports: NodePorts | None, /
) -> NodeExecution:
    """Execute the B2.22 business responsibility."""

    del state, ports
    raise RuntimeError("B2.22 is a mounted A3 subgraph, not an executable handler")


__all__ = ["handle_b2_22_invoke_exact_compiled_a3_subgraph_origin_automatic"]
