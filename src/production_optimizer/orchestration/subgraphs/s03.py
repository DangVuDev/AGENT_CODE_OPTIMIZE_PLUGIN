# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import S03_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_s03_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, S03_NODE_IDS)
    builder.add_edge(START, "S03.10")
    add_routed_edge(builder, "S03.10", {"continue": "S03.20", "rejected": END})
    add_linear_edges(builder, S03_NODE_IDS[1:6])
    add_routed_edge(builder, "S03.60", {"continue": "S03.70", "rejected": END})
    add_routed_edge(builder, "S03.70", {"continue": "S03.80", "rejected": END})
    builder.add_edge("S03.80", "S03.90")
    builder.add_edge("S03.90", END)
    return builder.compile()
