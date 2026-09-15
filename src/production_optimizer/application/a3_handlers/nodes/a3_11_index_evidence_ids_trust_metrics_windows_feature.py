# pyright: reportPrivateUsage=false
"""Implementation of business node A3.11."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import EvidenceBundle
from production_optimizer.contracts.a3 import EvidenceCatalog, EvidenceCatalogEntry
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a3_11_index_evidence_ids_trust_metrics_windows_feature(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)

    entries: list[EvidenceCatalogEntry] = []
    by_metric: dict[str, list[str]] = {}
    for item in bundle.evidence:
        entries.append(
            EvidenceCatalogEntry(
                evidence_id=item.evidence_id,
                metric_id=item.evidence_type,
                trust_level=item.trust_level,
                observed_at=item.identity.observed_at,
            )
        )
        by_metric.setdefault(item.evidence_type, []).append(item.evidence_id)

    catalog = _seal(
        EvidenceCatalog(
            **_base_envelope(state, "EvidenceCatalog", parents=[bundle.content_digest]),
            entries=entries,
            by_metric=by_metric,
        )
    )
    ref = _put_envelope(ports, state, catalog, node_id="A3.11")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_11_index_evidence_ids_trust_metrics_windows_feature"]
