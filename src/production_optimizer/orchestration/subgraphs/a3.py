# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import A3_FAN_OUT, A3_NODE_IDS

from .common import (
    END,
    START,
    StateGraph,
    add_fan_out,
    add_linear_edges,
    add_nodes,
    add_routed_edge,
)


def build_a3_graph(runtime: NodeRuntime, *, checkpointer: Any | None = None) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, A3_NODE_IDS)
    builder.add_edge(START, "A3.10")
    add_linear_edges(builder, A3_NODE_IDS[:4])
    add_fan_out(builder, A3_FAN_OUT)
    add_linear_edges(builder, A3_NODE_IDS[8:12])
    add_routed_edge(builder, "A3.51", {"continue": "A3.60", "rejected": END})
    add_linear_edges(builder, A3_NODE_IDS[12:20])
    add_routed_edge(
        builder,
        "A3.81",
        {"continue": "A3.90", "revision": "A3.82", "rejected": END},
    )
    add_routed_edge(builder, "A3.82", {"continue": "A3.60", "rejected": END})
    builder.add_edge("A3.90", END)
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
