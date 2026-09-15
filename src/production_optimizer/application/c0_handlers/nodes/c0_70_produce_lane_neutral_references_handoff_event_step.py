# pyright: reportPrivateUsage=false
"""Implementation of business node C0.70."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.c0 import ConvergedCase
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _origin,
    _put_envelope,
    _require_ref,
    _seal,
)


def handle_c0_70_produce_lane_neutral_references_handoff_event_step(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Seal the lane-neutral `ConvergedCase` handoff -- only reached once
    C0.60 has actually routed `continue`."""

    request_ref = _require_ref(state, "OptimizationRequest")
    source_ref = _require_ref(state, "SourceSnapshot")
    baseline_ref = _require_ref(state, "BaselineSnapshot")
    evidence_ref = _require_ref(state, "EvidenceBundle")
    portfolio_ref = _require_ref(state, "SolutionPortfolio")
    decision_ref = _require_ref(state, "ConvergenceDecision")

    converged_case = _seal(
        ConvergedCase(
            **_base_envelope(
                state,
                "ConvergedCase",
                parents=[
                    request_ref.content_digest,
                    source_ref.content_digest,
                    baseline_ref.content_digest,
                    evidence_ref.content_digest,
                    portfolio_ref.content_digest,
                    decision_ref.content_digest,
                ],
            ),
            origin=cast("Any", _origin(state)),
            request_digest=request_ref.content_digest,
            source_snapshot_digest=source_ref.content_digest,
            baseline_digest=baseline_ref.content_digest,
            evidence_bundle_digest=evidence_ref.content_digest,
            solution_portfolio_digest=portfolio_ref.content_digest,
            convergence_decision_digest=decision_ref.content_digest,
        )
    )
    ref = _put_envelope(ports, state, converged_case, node_id="C0.70")
    return NodeExecution(
        updates={"artifact_refs": [ref], "convergence_ref": ref, "status": "converged"}
    )


__all__ = ["handle_c0_70_produce_lane_neutral_references_handoff_event_step"]
