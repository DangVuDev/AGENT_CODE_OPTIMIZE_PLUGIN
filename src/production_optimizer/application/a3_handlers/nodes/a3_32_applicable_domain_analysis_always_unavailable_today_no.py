# pyright: reportPrivateUsage=false
"""Implementation of business node A3.32."""

from __future__ import annotations

from datetime import UTC, datetime

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.a3 import AnalyzerObservationBranch, PrioritizedSignalSet
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
    _stage_envelope,
)


def handle_a3_32_applicable_domain_analysis_always_unavailable_today_no(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Always empty today — no execution path for a registered domain analyzer.

    `ports.registry` (`RegistryKind.ANALYZER`) can name a domain analyzer,
    but nothing here can invoke one yet. Honestly-empty, like A2.62/A2.63,
    not a stub.
    """

    from production_optimizer.contracts.registries import RegistryKind

    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )

    reason = "no domain analyzer registered"
    if ports.registry is not None:
        tenant_id = _required_state_str(state, "tenant_id")
        active = ports.registry.list_active(
            tenant_id=tenant_id, registry_kind=RegistryKind.ANALYZER, at=datetime.now(UTC)
        )
        if active:
            reason = (
                f"{len(active)} domain analyzer registration(s) exist but "
                "no execution path is wired"
            )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.32",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.32",
            source="domain",
            observations=[],
            coverage_gaps=[],
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.32")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_32_applicable_domain_analysis_always_unavailable_today_no"]
