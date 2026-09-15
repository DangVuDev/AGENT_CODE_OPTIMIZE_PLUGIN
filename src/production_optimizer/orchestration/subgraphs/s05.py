# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import S05_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_s05_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, S05_NODE_IDS)
    builder.add_edge(START, "S05.10")
    add_linear_edges(builder, S05_NODE_IDS[0:3])  # S05.10 -> S05.20 -> S05.30
    add_routed_edge(builder, "S05.30", {"continue": "S05.40", "rejected": END})
    add_linear_edges(builder, S05_NODE_IDS[3:7])  # S05.40 -> S05.50 -> S05.60 -> S05.70
    add_routed_edge(builder, "S05.70", {"continue": "S05.80", "rejected": END})
    add_linear_edges(builder, S05_NODE_IDS[7:9])  # S05.80 -> S05.90
    builder.add_edge("S05.90", END)
    return builder.compile()
