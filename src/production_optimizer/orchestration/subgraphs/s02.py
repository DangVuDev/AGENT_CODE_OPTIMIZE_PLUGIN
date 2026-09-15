# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import S02_NODE_IDS

from .common import END, START, StateGraph, add_linear_edges, add_nodes, add_routed_edge


def build_s02_graph(runtime: NodeRuntime, *, checkpointer: Any | None = None) -> Any:
    builder = StateGraph(OptimizationState)
    add_nodes(builder, runtime, S02_NODE_IDS)
    builder.add_edge(START, "S02.10")
    add_linear_edges(builder, S02_NODE_IDS[0:9])
    add_routed_edge(
        builder, "S02.81", {"continue": "S02.90", "revision": "S02.30", "rejected": END}
    )
    add_routed_edge(builder, "S02.90", {"continue": END, "approval": END, "rejected": END})
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
