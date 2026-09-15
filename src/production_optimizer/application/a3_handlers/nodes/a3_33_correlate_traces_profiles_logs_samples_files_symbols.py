# pyright: reportPrivateUsage=false
"""Implementation of business node A3.33."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import BranchEvidenceRefs, SourceSnapshot
from production_optimizer.contracts.a3 import AnalyzerObservationBranch, PrioritizedSignalSet
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _seal,
    _stage_envelope,
)


def handle_a3_33_correlate_traces_profiles_logs_samples_files_symbols(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Correlates runtime telemetry — always empty today since A2.63 is."""

    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )

    try:
        telemetry_ref = _require_stage_ref(state, "A2.63", "BranchEvidenceRefs")
        telemetry_branch = _read_model(ports, state, telemetry_ref, BranchEvidenceRefs)
    except ValueError:
        telemetry_branch = None

    reason = (
        telemetry_branch.unavailable_reason
        if telemetry_branch is not None
        else "no A2.63 telemetry branch found in state"
    )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.33",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.33",
            source="runtime",
            observations=[],
            coverage_gaps=[],
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.33")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_33_correlate_traces_profiles_logs_samples_files_symbols"]
