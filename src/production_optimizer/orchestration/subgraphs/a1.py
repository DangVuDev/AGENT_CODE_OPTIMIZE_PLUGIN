# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import A1_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_a1_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, A1_NODE_IDS)
    builder.add_edge(START, "A1.10")
    add_linear_edges(builder, A1_NODE_IDS[:12])
    add_routed_edge(
        builder,
        "A1.80",
        {"continue": "A1.90", "clarification": END, "rejected": END},
    )
    add_routed_edge(
        builder,
        "A1.90",
        {"continue": "A1.95", "approval": END, "rejected": END},
    )
    builder.add_edge("A1.95", END)
    return builder.compile()
