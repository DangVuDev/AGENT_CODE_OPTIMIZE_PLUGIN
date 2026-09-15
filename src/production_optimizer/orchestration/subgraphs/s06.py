# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import S06_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_s06_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, S06_NODE_IDS)
    builder.add_edge(START, "S06.10")
    add_routed_edge(builder, "S06.10", {"continue": "S06.20", "rejected": END})
    add_linear_edges(builder, S06_NODE_IDS[1:8])  # S06.20 -> ... -> S06.80
    add_routed_edge(builder, "S06.80", {"continue": "S06.90"})
    add_routed_edge(builder, "S06.90", {"continue": END, "approval": END, "rejected": END})
    return builder.compile()
