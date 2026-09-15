# pyright: reportPrivateUsage=false
"""Implementation of business node B1.32."""

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import _query_branch


def handle_b1_32_execute_registered_bounded_metrics_queries_frozen_window(
    state: OptimizationState, ports: NodePorts | None, /
) -> NodeExecution:
    if ports is None:
        raise RuntimeError("production B1 handlers require configured node ports")
    return _query_branch(state, ports, node_id="B1.32", query_kind="metrics")


__all__ = ["handle_b1_32_execute_registered_bounded_metrics_queries_frozen_window"]
