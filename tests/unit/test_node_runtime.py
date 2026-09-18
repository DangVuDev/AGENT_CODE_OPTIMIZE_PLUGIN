# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import cast

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
from production_optimizer.application.node_runtime import _derive_idempotency_key
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


def test_idempotency_key_ignores_another_stages_revision_counter() -> None:
    """Regression test for a real crash found via `scripts/run.py`'s `full`
    tag: it runs S01/S02 as standalone graphs first (to print the sealed
    plan), then invokes `build_shared_workflow_graph`, whose compiled shape
    always starts at `START -> S01` and so revisits S01's already-completed
    nodes. If S02's own redraft loop had left `s02_revision_attempts`
    nonzero by then, an unscoped suffix would leak into S01's nodes' keys
    too (`_derive_idempotency_key` used to append every one of the three
    revision-counter suffixes to every node unconditionally), missing the
    cache and forcing a real recompute -- which reseals `RankingResult` with
    a fresh `created_at`/digest under the same pass-scoped `artifact_id`,
    hard-conflicting with the one already cached from the first run via
    `merge_artifact_refs`. A stage's own revision counter must still apply
    to its own nodes (the second assertion) -- this is a scoping fix, not a
    removal."""

    spec = _spec("S01.80", {"continue"})
    state = cast(
        "OptimizationState",
        {"case_id": "OPT-1", "s02_revision_attempts": 3, "s03_revision_attempts": 5},
    )
    assert _derive_idempotency_key(spec, state) == "OPT-1:S01.80:1"

    s02_spec = _spec("S02.30", {"continue"})
    assert _derive_idempotency_key(s02_spec, state) == "OPT-1:S02.30:1:rev3"


def test_node_ports_optional_capabilities_default_to_none() -> None:
    class _StubArtifacts:
        pass

    class _StubIntents:
        pass

    ports = NodePorts(artifacts=_StubArtifacts(), intents=_StubIntents())  # type: ignore[arg-type]

    assert ports.telemetry is None
    assert ports.policy is None
    assert ports.secrets is None
    assert ports.workers is None
    assert ports.registry is None
