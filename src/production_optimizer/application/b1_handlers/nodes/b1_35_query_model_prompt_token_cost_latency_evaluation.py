# pyright: reportPrivateUsage=false
"""Implementation of business node B1.35."""

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import _query_branch


def handle_b1_35_query_model_prompt_token_cost_latency_evaluation(
    state: OptimizationState, ports: NodePorts | None, /
) -> NodeExecution:
    if ports is None:
        raise RuntimeError("production B1 handlers require configured node ports")
    return _query_branch(state, ports, node_id="B1.35", query_kind="llm_evidence")


__all__ = ["handle_b1_35_query_model_prompt_token_cost_latency_evaluation"]
