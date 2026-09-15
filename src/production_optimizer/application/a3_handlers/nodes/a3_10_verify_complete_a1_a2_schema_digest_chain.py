# pyright: reportPrivateUsage=false
"""Implementation of business node A3.10."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    BaselineSnapshot,
    ComparabilityReport,
    EvidenceBundle,
    EvidenceQualityReport,
    SourceSnapshot,
)
from production_optimizer.contracts.a3 import A3IntakeDecision
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a3_10_verify_complete_a1_a2_schema_digest_chain(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    quality = _read_model(
        ports, state, _require_ref(state, "EvidenceQualityReport"), EvidenceQualityReport
    )
    comparability = _read_model(
        ports, state, _require_ref(state, "ComparabilityReport"), ComparabilityReport
    )
    baseline = _read_model(ports, state, _require_ref(state, "BaselineSnapshot"), BaselineSnapshot)

    mismatches: list[str] = []
    if bundle.baseline_digest != snapshot.content_digest:
        mismatches.append("EvidenceBundle.baseline_digest does not match SourceSnapshot digest")
    if baseline.source_snapshot_digest != snapshot.content_digest:
        mismatches.append(
            "BaselineSnapshot.source_snapshot_digest does not match SourceSnapshot digest"
        )
    if baseline.request_digest != request.content_digest:
        mismatches.append(
            "BaselineSnapshot.request_digest does not match OptimizationRequest digest"
        )

    decision = _seal(
        A3IntakeDecision(
            **_base_envelope(
                state,
                "A3IntakeDecision",
                parents=[request.content_digest, bundle.content_digest, baseline.content_digest],
            ),
            verified=not mismatches,
            mismatches=mismatches,
            gates_passed=quality.passed and comparability.comparable,
        )
    )
    ref = _put_envelope(ports, state, decision, node_id="A3.10")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_10_verify_complete_a1_a2_schema_digest_chain"]
