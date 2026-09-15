# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import S04_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_s04_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, S04_NODE_IDS)
    builder.add_edge(START, "S04.10")
    add_routed_edge(builder, "S04.10", {"continue": "S04.20", "rejected": END})
    add_linear_edges(builder, S04_NODE_IDS[1:8])
    add_routed_edge(builder, "S04.80", {"continue": "S04.90", "revision": "S04.90"})
    builder.add_edge("S04.90", END)
    return builder.compile()
