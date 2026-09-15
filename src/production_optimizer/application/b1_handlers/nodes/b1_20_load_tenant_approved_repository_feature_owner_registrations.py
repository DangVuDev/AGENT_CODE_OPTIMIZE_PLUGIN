# pyright: reportPrivateUsage=false
"""Implementation of business node B1.20."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import RegisteredSourceSet
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_b1_20_load_tenant_approved_repository_feature_owner_registrations(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Re-seal the case's own registered-source allowlist.

    A `RegisteredSourceSet` seed must already be in `artifact_refs` (the
    same shape as `ManualCasePayload` seeding A1) -- whatever triggers an
    automatic scan (a scheduler, an admin sync job) is responsible for
    declaring which feature/repository pairs discovery may consider. B1
    never invents a source to scan.
    """

    seed_ref = _require_ref(state, "RegisteredSourceSet")
    seed = _read_model(ports, state, seed_ref, RegisteredSourceSet)
    registry = _seal(
        RegisteredSourceSet(
            **_base_envelope(state, "RegisteredSourceSet", parents=[seed_ref.content_digest]),
            sources=seed.sources,
        )
    )
    ref = _put_envelope(ports, state, registry, node_id="B1.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_b1_20_load_tenant_approved_repository_feature_owner_registrations"]
