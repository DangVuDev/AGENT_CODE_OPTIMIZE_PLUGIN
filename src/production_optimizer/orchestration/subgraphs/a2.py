# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import A2_FAN_OUT, A2_NODE_IDS

from .common import (
    END,
    START,
    StateGraph,
    add_fan_out,
    add_linear_edges,
    add_nodes,
    add_routed_edge,
)


def build_a2_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, A2_NODE_IDS)
    builder.add_edge(START, "A2.10")
    add_linear_edges(builder, A2_NODE_IDS[:4])
    add_routed_edge(builder, "A2.31", {"continue": "A2.40", "approval": END})
    add_linear_edges(builder, A2_NODE_IDS[4:7])
    add_fan_out(builder, A2_FAN_OUT)
    add_linear_edges(builder, A2_NODE_IDS[12:16])
    add_routed_edge(
        builder,
        "A2.90",
        {"continue": "A2.91", "missing": END, "rejected": END},
    )
    add_routed_edge(
        builder,
        "A2.91",
        {"continue": "A2.95", "missing": END, "incomparable": END},
    )
    builder.add_edge("A2.95", END)
    return builder.compile()
