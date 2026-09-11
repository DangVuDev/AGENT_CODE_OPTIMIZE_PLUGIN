# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import pairwise
from typing import Any

from langgraph.graph import END, START, StateGraph

from production_optimizer.application.node_runtime import NodeRuntime, node_callable, route_for
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import FanOut


def add_nodes(builder: Any, runtime: NodeRuntime, node_ids: Sequence[str]) -> None:
    for node_id in node_ids:
        builder.add_node(node_id, node_callable(runtime, node_id))


def add_linear_edges(
    builder: Any,
    node_ids: Sequence[str],
) -> None:
    for source, target in pairwise(node_ids):
        builder.add_edge(source, target)


def add_fan_out(
    builder: Any,
    fan_out: FanOut,
) -> None:
    for branch in fan_out.branches:
        builder.add_edge(fan_out.source, branch)
    builder.add_edge(list(fan_out.branches), fan_out.join)


def add_routed_edge(
    builder: Any,
    source: str,
    routes: dict[str, str],
) -> None:
    builder.add_conditional_edges(source, route_for(source), routes)


def completed(required_node: str) -> Callable[[OptimizationState], str]:
    def select(state: OptimizationState) -> str:
        return "continue" if required_node in state.get("completed_nodes", []) else "halt"

    return select


__all__ = [
    "END",
    "START",
    "StateGraph",
    "add_fan_out",
    "add_linear_edges",
    "add_nodes",
    "add_routed_edge",
    "completed",
]
