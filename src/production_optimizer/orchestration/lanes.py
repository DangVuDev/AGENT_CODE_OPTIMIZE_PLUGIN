# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState

from .subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_b1_graph,
    build_b2_graph,
    build_c0_graph,
)
from .subgraphs.common import completed


def build_lane_a_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    builder.add_node("A1", build_a1_graph(runtime))
    builder.add_node("A2", build_a2_graph(runtime))
    builder.add_node("A3", build_a3_graph(runtime))
    builder.add_node("C0", build_c0_graph(runtime))
    builder.add_edge(START, "A1")
    builder.add_conditional_edges("A1", completed("A1.95"), {"continue": "A2", "halt": END})
    builder.add_conditional_edges("A2", completed("A2.95"), {"continue": "A3", "halt": END})
    builder.add_conditional_edges("A3", completed("A3.90"), {"continue": "C0", "halt": END})
    builder.add_edge("C0", END)
    return builder.compile()


def build_lane_b_discovery_graph(runtime: NodeRuntime) -> Any:
    return build_b1_graph(runtime)


def build_lane_b_proposal_graph(runtime: NodeRuntime) -> Any:
    builder = StateGraph(OptimizationState)
    builder.add_node("B2", build_b2_graph(runtime))
    builder.add_node("C0", build_c0_graph(runtime))
    builder.add_edge(START, "B2")
    builder.add_conditional_edges("B2", completed("B2.60"), {"continue": "C0", "halt": END})
    builder.add_edge("C0", END)
    return builder.compile()


LaneBRoute = Literal["discovery", "qualified"]


def _route_lane_b_entrypoint(state: OptimizationState) -> LaneBRoute:
    entrypoint = state.get("entrypoint")
    if entrypoint == "discovery":
        return "discovery"
    if entrypoint == "qualified":
        return "qualified"
    raise ValueError(
        "Lane B requires entrypoint 'discovery' or 'qualified', "
        f"got {entrypoint!r}"
    )


def build_lane_b_graph(runtime: NodeRuntime) -> Any:
    """Build the single logical Lane B with scan and case entrypoints.

    Discovery scans and qualified proposal cases deliberately remain separate
    executions. B1.96 persists an outbox handoff; its dispatcher starts a new
    root invocation with the ``qualified`` entrypoint, which enters B2 here.
    """

    builder = StateGraph(OptimizationState)
    builder.add_node("B1", build_lane_b_discovery_graph(runtime))
    builder.add_node("B2_proposal", build_lane_b_proposal_graph(runtime))
    builder.add_conditional_edges(
        START,
        _route_lane_b_entrypoint,
        {"discovery": "B1", "qualified": "B2_proposal"},
    )
    builder.add_edge("B1", END)
    builder.add_edge("B2_proposal", END)
    return builder.compile()


__all__ = [
    "build_lane_a_graph",
    "build_lane_b_discovery_graph",
    "build_lane_b_graph",
    "build_lane_b_proposal_graph",
]
