"""Named production entry point for business node B1.95."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState


def handle_b1_95_invoke_exact_compiled_a2_subgraph_historical_recovery(
    state: OptimizationState, ports: NodePorts | None, /
) -> NodeExecution:
    """Execute the B1.95 business responsibility."""

    del state, ports
    raise RuntimeError("B1.95 is a mounted A2 subgraph, not an executable handler")


__all__ = ["handle_b1_95_invoke_exact_compiled_a2_subgraph_historical_recovery"]
