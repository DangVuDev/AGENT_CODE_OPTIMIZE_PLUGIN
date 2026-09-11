from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from langgraph.constants import END, START

from production_optimizer.application import (
    NodeExecution,
    NodeNotEnabledError,
    NodeRoute,
    NodeRuntime,
    NodeSpec,
    RegisteredNode,
    SideEffectClass,
)
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration import ALL_BUSINESS_NODE_IDS, build_root_graph
from production_optimizer.orchestration.catalog import (
    A1_NODE_IDS,
    A2_NODE_IDS,
    A3_NODE_IDS,
    B1_NODE_IDS,
    B2_NODE_IDS,
    C0_NODE_IDS,
)
from production_optimizer.orchestration.subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_b1_graph,
    build_b2_graph,
    build_c0_graph,
)


def _handler(route: NodeRoute = NodeRoute.CONTINUE) -> Callable[..., NodeExecution]:
    def execute(_state: OptimizationState, _ports: object, /) -> NodeExecution:
        return NodeExecution(route=route)

    return execute


def _runtime(overrides: dict[str, NodeRoute] | None = None) -> NodeRuntime:
    selected = overrides or {}
    registrations: dict[str, RegisteredNode] = {}
    for node_id in ALL_BUSINESS_NODE_IDS:
        route = selected.get(node_id, NodeRoute.CONTINUE)
        registrations[node_id] = RegisteredNode(
            spec=NodeSpec(
                node_id=node_id,
                business_task_id=node_id,
                owner="contract-test",
                input_contract=f"{node_id}Input@1.0",
                output_contract=f"{node_id}Output@1.0",
                supported_schema_majors={1},
                idempotency_key_version="1",
                side_effect_class=SideEffectClass.PURE,
                timeout_seconds=5,
                max_attempts=1,
                allowed_routes={NodeRoute.CONTINUE.value, route.value},
                runbook=f"docs/runbooks/{node_id}.md",
                slo="contract fixture",
            ),
            handler=_handler(route),
        )
    return NodeRuntime(registrations)


def _business_nodes(graph: Any) -> set[str]:
    names: set[str] = set()
    for depth in (False, 1, 2, 3):
        drawable = graph.get_graph(xray=depth)  # type: ignore[attr-defined]
        names.update(name.split(":")[-1] for name in drawable.nodes)
    return names & ALL_BUSINESS_NODE_IDS


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (build_a1_graph, set(A1_NODE_IDS)),
        (build_a2_graph, set(A2_NODE_IDS)),
        (build_a3_graph, set(A3_NODE_IDS)),
        (build_b1_graph, set(B1_NODE_IDS) | set(A2_NODE_IDS)),
        (build_b2_graph, set(B2_NODE_IDS) | set(A3_NODE_IDS)),
        (build_c0_graph, set(C0_NODE_IDS)),
    ],
)
def test_subgraph_business_inventory(
    builder: Callable[[NodeRuntime], Any], expected: set[str]
) -> None:
    compiled = builder(NodeRuntime())
    assert _business_nodes(compiled) == expected


def test_root_graph_routes_only_through_lane_subgraphs() -> None:
    drawable = build_root_graph().get_graph()
    assert set(drawable.nodes) == {
        START,
        "initialize_case",
        "lane_a",
        "lane_b_discovery",
        "lane_b_proposal",
        END,
    }
    assert _business_nodes(build_root_graph()) == ALL_BUSINESS_NODE_IDS


def test_unregistered_business_nodes_fail_closed() -> None:
    with pytest.raises(NodeNotEnabledError, match=r"A1\.10"):
        build_root_graph().invoke(
            {
                "case_id": "OPT-1",
                "thread_id": "THREAD-1",
                "tenant_id": "TENANT-1",
                "entrypoint": "manual",
                "lane": "manual",
            }
        )


def test_manual_happy_path_crosses_shared_a2_a3_and_c0() -> None:
    result = build_root_graph(runtime=_runtime()).invoke(
        {
            "case_id": "OPT-1",
            "thread_id": "THREAD-1",
            "tenant_id": "TENANT-1",
            "entrypoint": "manual",
            "lane": "manual",
        }
    )
    completed = set(result["completed_nodes"])
    assert {"A1.95", "A2.95", "A3.90", "C0.70"} <= completed
    assert not completed & set(B1_NODE_IDS)
    assert not completed & set(B2_NODE_IDS)
    assert result["baseline_mode"] == "active_collection"


def test_a1_clarification_stops_before_a2() -> None:
    result = build_root_graph(runtime=_runtime({"A1.80": NodeRoute.CLARIFICATION})).invoke(
        {
            "case_id": "OPT-1",
            "thread_id": "THREAD-1",
            "tenant_id": "TENANT-1",
            "entrypoint": "manual",
            "lane": "manual",
        }
    )
    completed = set(result["completed_nodes"])
    assert "A1.80" in completed
    assert "A1.90" not in completed
    assert not completed & set(A2_NODE_IDS)


def test_discovery_path_reuses_a2_in_historical_recovery_mode() -> None:
    result = build_root_graph(runtime=_runtime()).invoke(
        {
            "case_id": "SCAN-1",
            "thread_id": "SCAN-THREAD-1",
            "tenant_id": "TENANT-1",
            "entrypoint": "discovery",
            "lane": "automatic",
        }
    )
    completed = set(result["completed_nodes"])
    assert {"B1.91", "A2.95", "B1.96"} <= completed
    assert not completed & set(A1_NODE_IDS)
    assert not completed & set(A3_NODE_IDS)
    assert result["baseline_mode"] == "historical_recovery"


def test_qualified_case_reuses_a3_before_b2_handoff_and_c0() -> None:
    result = build_root_graph(runtime=_runtime()).invoke(
        {
            "case_id": "OPT-2",
            "thread_id": "CASE-THREAD-2",
            "tenant_id": "TENANT-1",
            "entrypoint": "qualified",
            "lane": "automatic",
        }
    )
    completed = set(result["completed_nodes"])
    assert {"B2.21", "A3.90", "B2.60", "C0.70"} <= completed
    assert not completed & set(A1_NODE_IDS)
    assert not completed & set(B1_NODE_IDS)


def test_root_rejects_lane_and_entrypoint_mismatch() -> None:
    with pytest.raises(ValueError, match="requires lane"):
        build_root_graph(runtime=_runtime()).invoke(
            {
                "case_id": "OPT-1",
                "thread_id": "THREAD-1",
                "tenant_id": "TENANT-1",
                "entrypoint": "manual",
                "lane": "automatic",
            }
        )
