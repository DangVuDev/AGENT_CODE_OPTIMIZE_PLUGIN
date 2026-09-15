# pyright: reportPrivateUsage=false
"""Implementation of business node B1.96."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import (
    DetectionReport,
    OwnershipBinding,
    QualifiedOpportunity,
    SourceBinding,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_b1_96_seal_scan_signal_request_baseline_owner_policy(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    detection_report_ref = _require_ref(state, "DetectionReport")
    request_ref = _require_ref(state, "OptimizationRequest")
    source_snapshot_ref = _require_ref(state, "SourceSnapshot")
    baseline_ref = _require_ref(state, "BaselineSnapshot")
    evidence_bundle_ref = _require_ref(state, "EvidenceBundle")
    comparability_ref = _require_ref(state, "ComparabilityReport")
    report = _read_model(ports, state, detection_report_ref, DetectionReport)

    source_binding = (
        report.source_bindings[0]
        if report.source_bindings
        else SourceBinding(
            binding_id="none",
            repository_id="unknown",
            resolved=False,
            unresolved_reason="no source binding available",
        )
    )
    owner_binding = (
        report.ownership_bindings[0]
        if report.ownership_bindings
        else OwnershipBinding(binding_id="none", resolved=False)
    )

    opportunity = _seal(
        QualifiedOpportunity(
            **_base_envelope(
                state,
                "QualifiedOpportunity",
                parents=[
                    detection_report_ref.content_digest,
                    request_ref.content_digest,
                    baseline_ref.content_digest,
                ],
            ),
            detection_report_digest=detection_report_ref.content_digest,
            request_digest=request_ref.content_digest,
            source_snapshot_digest=source_snapshot_ref.content_digest,
            baseline_digest=baseline_ref.content_digest,
            evidence_bundle_digest=evidence_bundle_ref.content_digest,
            comparability_report_digest=comparability_ref.content_digest,
            source_binding=source_binding,
            owner_binding=owner_binding,
            case_start_id=f"case-start-{_required_state_str(state, 'case_id')}",
        )
    )
    ref = _put_envelope(ports, state, opportunity, node_id="B1.96")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_b1_96_seal_scan_signal_request_baseline_owner_policy"]
