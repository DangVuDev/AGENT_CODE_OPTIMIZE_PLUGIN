# pyright: reportPrivateUsage=false
"""Implementation of business node B2.31."""

from __future__ import annotations

from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.b1 import ObservedSourceIdentity, RegisteredSourceSet
from production_optimizer.contracts.b2 import StalenessDecision
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _git,
    _read_model,
    _required_state_str,
    _try_ref,
)


def handle_b2_31_compare_current_source_registry_evidence_policy_versions(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real staleness check: does the source registered for this scan still
    have the same git revision B1.21 fingerprinted it at?"""

    identity_ref = _try_ref(state, "ObservedSourceIdentity")
    registry_ref = _try_ref(state, "RegisteredSourceSet")

    stale = False
    changed_dimensions: list[str] = []
    if identity_ref is not None and registry_ref is not None:
        identity = _read_model(ports, state, identity_ref, ObservedSourceIdentity)
        registry = _read_model(ports, state, registry_ref, RegisteredSourceSet)
        source = next(
            (s for s in registry.sources if s.repository_id == identity.repository_id), None
        )
        if source is not None and Path(source.local_path).exists() and identity.git_revision:
            current_revision = _git(Path(source.local_path), "rev-parse", "HEAD")
            if current_revision is not None and current_revision != identity.git_revision:
                stale = True
                changed_dimensions.append("git_revision")

    decision = StalenessDecision(
        decision_id=f"staleness-{_required_state_str(state, 'case_id')}",
        stale=stale,
        changed_dimensions=changed_dimensions,
        action="refresh" if stale else "proceed",
    )
    route = NodeRoute.REFRESH if stale else NodeRoute.CONTINUE
    return NodeExecution(route=route, updates={"b2_staleness_decision": decision})


__all__ = ["handle_b2_31_compare_current_source_registry_evidence_policy_versions"]
