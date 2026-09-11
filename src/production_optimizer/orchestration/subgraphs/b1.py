# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import (
    B1_DETECTOR_FAN_OUT,
    B1_NODE_IDS,
    B1_QUERY_FAN_OUT,
)

from .a2 import build_a2_graph
from .common import (
    END,
    START,
    StateGraph,
    add_fan_out,
    add_linear_edges,
    add_nodes,
    add_routed_edge,
    completed,
)


def build_b1_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    native_nodes = tuple(node_id for node_id in B1_NODE_IDS if node_id != "B1.95")
    add_nodes(builder, runtime, native_nodes)
    builder.add_node("B1.95", build_a2_graph(runtime))

    builder.add_edge(START, "B1.10")
    add_linear_edges(builder, B1_NODE_IDS[:5])
    add_fan_out(builder, B1_QUERY_FAN_OUT)
    builder.add_edge("B1.40", "B1.41")
    add_fan_out(builder, B1_DETECTOR_FAN_OUT)
    add_linear_edges(builder, B1_NODE_IDS[15:20])
    add_routed_edge(
        builder,
        "B1.71",
        {"continue": "B1.80", "quarantine": END, "rejected": END},
    )
    add_routed_edge(
        builder,
        "B1.80",
        {"continue": "B1.81", "merged": END, "suppressed": END},
    )
    add_routed_edge(
        builder,
        "B1.81",
        {"continue": "B1.90", "suppressed": END, "closed": END},
    )
    builder.add_edge("B1.90", "B1.91")
    add_routed_edge(
        builder,
        "B1.91",
        {"continue": "B1.95", "approval": END, "rejected": END},
    )
    builder.add_conditional_edges("B1.95", completed("A2.95"), {"continue": "B1.96", "halt": END})
    builder.add_edge("B1.96", END)
    return builder.compile()
