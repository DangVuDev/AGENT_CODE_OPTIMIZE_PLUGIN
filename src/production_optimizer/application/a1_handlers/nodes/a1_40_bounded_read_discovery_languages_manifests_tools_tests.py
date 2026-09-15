# pyright: reportPrivateUsage=false
"""Implementation of business node A1.40."""

from __future__ import annotations

from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import LocalSourceIdentity
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _discover_project_profile,
    _put_envelope,
    _read_model,
    _require_ref,
)


def handle_a1_40_bounded_read_discovery_languages_manifests_tools_tests(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    source = _read_model(
        ports,
        state,
        _require_ref(state, "LocalSourceIdentity"),
        LocalSourceIdentity,
    )
    profile = _discover_project_profile(state, Path(source.canonical_path))
    ref = _put_envelope(ports, state, profile, node_id="A1.40")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_40_bounded_read_discovery_languages_manifests_tools_tests"]
