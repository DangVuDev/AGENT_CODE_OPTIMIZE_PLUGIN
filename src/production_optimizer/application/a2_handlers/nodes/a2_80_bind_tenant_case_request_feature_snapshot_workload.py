# pyright: reportPrivateUsage=false
"""Implementation of business node A2.80."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import (
    EvidenceBundle,
    NormalizedEvidenceSet,
    SourceSnapshot,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _EVIDENCE_TYPE_SOURCE_TYPE,
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a2_80_bind_tenant_case_request_feature_snapshot_workload(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Bind provenance into one `EvidenceBundle` for downstream A3 gates.

    `baseline_digest` is set to the `SourceSnapshot` digest: in this pilot's
    `active_collection` mode the snapshot *is* the baseline being measured
    (the same digest `BaselineSnapshot.source_snapshot_digest` uses at
    A2.95). No separate "baseline" artifact exists yet at this point in the
    graph to reference instead.
    """

    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    normalized = _read_model(
        ports, state, _require_ref(state, "NormalizedEvidenceSet"), NormalizedEvidenceSet
    )

    collector_versions = {
        item.identity.collector: item.identity.collector_version for item in normalized.evidence
    }
    coverage: dict[str, float] = {}
    for item in normalized.evidence:
        source_type = _EVIDENCE_TYPE_SOURCE_TYPE.get(item.evidence_type, item.evidence_type)
        coverage[source_type] = coverage.get(source_type, 0.0) + 1.0
        if item.requirement_id:
            key = f"requirement:{item.requirement_id}"
            coverage[key] = coverage.get(key, 0.0) + 1.0

    bundle = _seal(
        EvidenceBundle(
            **_base_envelope(
                state,
                "EvidenceBundle",
                parents=[snapshot.content_digest, normalized.content_digest],
            ),
            baseline_digest=snapshot.content_digest,
            evidence=normalized.evidence,
            collector_versions=collector_versions,
            coverage=coverage,
        )
    )
    ref = _put_envelope(ports, state, bundle, node_id="A2.80")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_80_bind_tenant_case_request_feature_snapshot_workload"]
