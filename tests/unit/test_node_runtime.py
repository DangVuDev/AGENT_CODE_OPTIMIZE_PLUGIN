from __future__ import annotations

import pytest

from production_optimizer.application import (
    BusinessNodeHandler,
    InvalidNodeExecutionError,
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    NodeSpec,
    RegisteredNode,
    SideEffectClass,
)
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration import build_root_graph


def _spec(node_id: str, routes: set[str]) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="test",
        input_contract="Input@1.0",
        output_contract="Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="1",
        side_effect_class=SideEffectClass.PURE,
        timeout_seconds=1,
        max_attempts=1,
        allowed_routes=routes,
        runbook="docs/runbooks/test.md",
        slo="test",
    )


def _handler(result: NodeExecution) -> BusinessNodeHandler:
    def execute(_state: OptimizationState, _ports: NodePorts | None, /) -> NodeExecution:
        return result

    return execute


def _runtime(node_id: str, result: NodeExecution) -> NodeRuntime:
    return NodeRuntime(
        {node_id: RegisteredNode(spec=_spec(node_id, {"continue"}), handler=_handler(result))}
    )


def test_runtime_rejects_registration_key_mismatch() -> None:
    with pytest.raises(ValueError, match="do not match"):
        NodeRuntime(
            {
                "A1.20": RegisteredNode(
                    spec=_spec("A1.10", {"continue"}),
                    handler=_handler(NodeExecution()),
                )
            }
        )


def test_runtime_rejects_undeclared_route() -> None:
    runtime = _runtime("A1.10", NodeExecution(route=NodeRoute.REJECTED))
    with pytest.raises(InvalidNodeExecutionError, match="undeclared route"):
        runtime.execute("A1.10", {})


@pytest.mark.parametrize("field", ["tenant_id", "case_id", "thread_id", "lane", "entrypoint"])
def test_runtime_protects_identity_fields(field: str) -> None:
    runtime = _runtime("A1.10", NodeExecution(updates={field: "replacement"}))
    with pytest.raises(InvalidNodeExecutionError, match="identity fields"):
        runtime.execute("A1.10", {})


def test_runtime_rejects_unknown_state_field() -> None:
    runtime = _runtime("A1.10", NodeExecution(updates={"raw_source": "secret"}))
    with pytest.raises(InvalidNodeExecutionError, match="unknown state fields"):
        runtime.execute("A1.10", {})


def test_root_rejects_non_catalog_registration() -> None:
    runtime = _runtime("X9.99", NodeExecution())
    with pytest.raises(ValueError, match="outside the canonical catalog"):
        build_root_graph(runtime=runtime)
