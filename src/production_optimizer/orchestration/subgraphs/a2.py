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
    add_linear_edges,
    add_nodes,
    add_routed_edge,
)


def _execution_routes(state: OptimizationState) -> list[str] | str:
    if state.get("node_routes", {}).get("A2.50") == "continue":
        return list(A2_FAN_OUT.branches)
    return "halt"


def build_a2_graph(runtime: NodeRuntime, *, checkpointer: Any | None = None) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, A2_NODE_IDS)
    builder.add_edge(START, "A2.10")
    add_routed_edge(builder, "A2.10", {"continue": "A2.20", "rejected": END})
    add_linear_edges(builder, A2_NODE_IDS[1:4])
    add_routed_edge(builder, "A2.31", {"continue": "A2.40", "approval": END})
    add_routed_edge(builder, "A2.40", {"continue": "A2.41", "missing": END})
    builder.add_edge("A2.41", "A2.50")
    builder.add_conditional_edges(
        "A2.50",
        _execution_routes,
        {
            **{branch: branch for branch in A2_FAN_OUT.branches},
            "halt": END,
        },
    )
    builder.add_edge(list(A2_FAN_OUT.branches), A2_FAN_OUT.join)
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
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
