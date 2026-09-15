# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import S01_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_s01_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, S01_NODE_IDS)
    builder.add_edge(START, "S01.10")
    builder.add_edge("S01.10", "S01.20")
    add_routed_edge(builder, "S01.20", {"continue": "S01.30", "rejected": END})
    add_linear_edges(builder, S01_NODE_IDS[2:8])
    add_routed_edge(builder, "S01.80", {"continue": "S01.90", "approval": END, "rejected": END})
    builder.add_edge("S01.90", END)
    return builder.compile()
