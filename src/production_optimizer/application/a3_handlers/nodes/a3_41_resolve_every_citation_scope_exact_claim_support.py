# pyright: reportPrivateUsage=false
"""Implementation of business node A3.41."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import EvidenceBundle
from production_optimizer.contracts.a3 import (
    CitationResolutionEntry,
    CitationResolutionReport,
    CitationResolutionReportSet,
    EvidenceCatalog,
    FindingDraftSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a3_41_resolve_every_citation_scope_exact_claim_support(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    draft_set = _read_model(ports, state, _require_ref(state, "FindingDraftSet"), FindingDraftSet)
    catalog = _read_model(ports, state, _require_ref(state, "EvidenceCatalog"), EvidenceCatalog)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)

    known_ids = {entry.evidence_id for entry in catalog.entries}
    values_by_id = {item.evidence_id: item.value for item in bundle.evidence}

    reports: list[CitationResolutionReport] = []
    for draft in draft_set.drafts:
        entries: list[CitationResolutionEntry] = []
        for evidence_id in draft.supporting_evidence_ids:
            resolved = evidence_id in known_ids
            in_scope = resolved
            value = values_by_id.get(evidence_id)
            supports = resolved and isinstance(value, int | float) and value != 0
            if resolved and in_scope and supports:
                reason = None
            elif not resolved:
                reason = "evidence id not found in catalog"
            else:
                reason = "evidence value does not support the claim (zero/neutral outcome)"
            entries.append(
                CitationResolutionEntry(
                    evidence_id=evidence_id,
                    resolved=resolved,
                    in_scope=in_scope,
                    supports_statement=supports,
                    reason=reason,
                )
            )
        reports.append(
            CitationResolutionReport(
                report_id=f"citation-{draft.finding_id}",
                finding_id=draft.finding_id,
                entries=entries,
                all_resolved=all(
                    e.resolved and e.in_scope and e.supports_statement for e in entries
                ),
            )
        )

    report_set = _seal(
        CitationResolutionReportSet(
            **_base_envelope(
                state,
                "CitationResolutionReportSet",
                parents=[draft_set.content_digest, catalog.content_digest],
            ),
            reports=reports,
        )
    )
    ref = _put_envelope(ports, state, report_set, node_id="A3.41")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_41_resolve_every_citation_scope_exact_claim_support"]
