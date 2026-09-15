# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import S07_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_s07_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, S07_NODE_IDS)
    builder.add_edge(START, "S07.10")
    add_routed_edge(builder, "S07.10", {"continue": "S07.20", "rejected": END})
    add_linear_edges(builder, S07_NODE_IDS[1:9])  # S07.20 -> ... -> S07.90
    add_routed_edge(builder, "S07.90", {"continue": END, "rejected": END})
    return builder.compile()
