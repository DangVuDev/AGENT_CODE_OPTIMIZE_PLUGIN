# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import B2_NODE_IDS

from .a3 import build_a3_graph
from .common import (
    END,
    START,
    StateGraph,
    add_linear_edges,
    add_nodes,
    add_routed_edge,
    completed,
)


def build_b2_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    native_nodes = tuple(node_id for node_id in B2_NODE_IDS if node_id != "B2.22")
    add_nodes(builder, runtime, native_nodes)
    builder.add_node("B2.22", build_a3_graph(runtime))

    builder.add_edge(START, "B2.10")
    add_linear_edges(builder, B2_NODE_IDS[:3])
    builder.add_edge("B2.21", "B2.22")
    builder.add_conditional_edges("B2.22", completed("A3.90"), {"continue": "B2.30", "halt": END})
    builder.add_edge("B2.30", "B2.31")
    add_routed_edge(
        builder,
        "B2.31",
        {"continue": "B2.40", "refresh": END, "rejected": END},
    )
    add_linear_edges(builder, B2_NODE_IDS[6:9])
    add_routed_edge(
        builder,
        "B2.50",
        {"continue": "B2.60", "approval": "B2.51", "rejected": END},
    )
    builder.add_edge("B2.51", "B2.52")
    add_routed_edge(
        builder,
        "B2.52",
        {"continue": "B2.60", "revision": "B2.22", "rejected": END},
    )
    builder.add_edge("B2.60", END)
    return builder.compile()
