# pyright: reportPrivateUsage=false
"""Implementation of business node B1.61."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import DetectionSignal, ObservedSourceIdentity, SourceBinding
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_model,
    _require_ref,
    _required_state_str,
)


def handle_b1_61_bind_deployment_trace_commit_available_local_git(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    if not signals:
        return NodeExecution(updates={"b1_source_bindings": []})
    identity = _read_model(
        ports, state, _require_ref(state, "ObservedSourceIdentity"), ObservedSourceIdentity
    )
    resolved = identity.repository_id != "none"
    binding = SourceBinding(
        binding_id=f"source-binding-{_required_state_str(state, 'case_id')}",
        repository_id=identity.repository_id,
        git_revision=identity.git_revision,
        resolved=resolved,
        unresolved_reason=None if resolved else "no registered source observed for this scan",
    )
    return NodeExecution(updates={"b1_source_bindings": [binding]})


__all__ = ["handle_b1_61_bind_deployment_trace_commit_available_local_git"]
