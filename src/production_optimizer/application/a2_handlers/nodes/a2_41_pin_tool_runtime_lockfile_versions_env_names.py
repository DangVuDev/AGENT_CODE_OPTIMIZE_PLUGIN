# pyright: reportPrivateUsage=false
"""Implementation of business node A2.41."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import EnvironmentManifest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _git,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a2_41_pin_tool_runtime_lockfile_versions_env_names(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)

    tool_versions = {
        "python": platform.python_version(),
        "git": _git(Path.cwd(), "--version").removeprefix("git version ").strip(),
    }

    manifest = _seal(
        EnvironmentManifest(
            **_base_envelope(state, "EnvironmentManifest", parents=[request.content_digest]),
            tool_versions={k: v for k, v in tool_versions.items() if v},
            hardware_profile=platform.platform(),
            concurrency=request.workload.concurrency,
            cache_state=request.workload.cache_state,
            dimensions={
                "os": platform.system(),
                "architecture": platform.machine(),
                "logical_cpu_count": str(max(os.cpu_count() or 1, 1)),
            },
        )
    )
    ref = _put_envelope(ports, state, manifest, node_id="A2.41")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_41_pin_tool_runtime_lockfile_versions_env_names"]
