# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import C0_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_c0_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, C0_NODE_IDS)
    builder.add_edge(START, "C0.10")
    add_linear_edges(builder, C0_NODE_IDS[:6])
    add_routed_edge(builder, "C0.60", {"continue": "C0.70", "rejected": END})
    builder.add_edge("C0.70", END)
    return builder.compile()
