# pyright: reportPrivateUsage=false
"""Implementation of business node A1.63."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import (
    ExecutionBudget,
    ExecutionBudgetArtifact,
    ManualCasePayload,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a1_63_apply_tenant_quota_policy_freeze_deadline_worker(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    budget = ExecutionBudget(
        deadline_seconds=payload.deadline_seconds or 300,
        maximum_worker_seconds=(
            payload.maximum_worker_seconds if payload.maximum_worker_seconds is not None else 180
        ),
        maximum_model_tokens=(
            payload.maximum_model_tokens if payload.maximum_model_tokens is not None else 4000
        ),
        maximum_storage_bytes=(
            payload.maximum_storage_bytes
            if payload.maximum_storage_bytes is not None
            else 50_000_000
        ),
        allowed_analyzers=(
            payload.allowed_analyzers
            if payload.allowed_analyzers is not None
            else {"tree-sitter", "semgrep"}
        ),
        maximum_concurrency=payload.concurrency or 1,
    )
    artifact = _seal(
        ExecutionBudgetArtifact(
            **_base_envelope(state, "ExecutionBudgetArtifact"),
            budget=budget,
            policy_version=payload.policy_version,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.63")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_63_apply_tenant_quota_policy_freeze_deadline_worker"]
