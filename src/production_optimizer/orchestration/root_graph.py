# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from production_optimizer.application import NodeRuntime
from production_optimizer.contracts.state import OptimizationState

from .catalog import ALL_BUSINESS_NODE_IDS
from .lanes import build_lane_a_graph, build_lane_b_discovery_graph, build_lane_b_proposal_graph

RootRoute = Literal["manual", "discovery", "qualified"]


def _initialize(state: OptimizationState) -> dict[str, str]:
    required = ("case_id", "thread_id", "tenant_id", "entrypoint", "lane")
    missing = [field for field in required if not state.get(field)]
    if missing:
        raise ValueError(f"root state is missing required identity fields: {missing}")

    entrypoint = state.get("entrypoint")
    lane = state.get("lane")
    expected_lane = "manual" if entrypoint == "manual" else "automatic"
    if lane != expected_lane:
        raise ValueError(f"entrypoint {entrypoint!r} requires lane {expected_lane!r}")

    mode = "active_collection" if entrypoint == "manual" else "historical_recovery"
    return {"status": "running", "current_node": "initialize_case", "baseline_mode": mode}


def _route_entrypoint(state: OptimizationState) -> RootRoute:
    entrypoint = state.get("entrypoint")
    if entrypoint is None:
        raise ValueError("root state is missing entrypoint")
    return entrypoint


def build_root_graph(*, runtime: NodeRuntime | None = None, checkpointer: Any | None = None) -> Any:
    """Build the two-lane root with fail-closed business-node registrations."""

    node_runtime = runtime or NodeRuntime()
    unknown_nodes = node_runtime.enabled_node_ids - ALL_BUSINESS_NODE_IDS
    if unknown_nodes:
        raise ValueError(
            "runtime contains task IDs outside the canonical catalog: "
            f"{sorted(unknown_nodes)}"
        )
    builder = StateGraph(OptimizationState)
    builder.add_node("initialize_case", _initialize)
    builder.add_node("lane_a", build_lane_a_graph(node_runtime))
    builder.add_node("lane_b_discovery", build_lane_b_discovery_graph(node_runtime))
    builder.add_node("lane_b_proposal", build_lane_b_proposal_graph(node_runtime))
    builder.add_edge(START, "initialize_case")
    builder.add_conditional_edges(
        "initialize_case",
        _route_entrypoint,
        {
            "manual": "lane_a",
            "discovery": "lane_b_discovery",
            "qualified": "lane_b_proposal",
        },
    )
    builder.add_edge("lane_a", END)
    builder.add_edge("lane_b_discovery", END)
    builder.add_edge("lane_b_proposal", END)
    return builder.compile(checkpointer=checkpointer)


graph = build_root_graph()
