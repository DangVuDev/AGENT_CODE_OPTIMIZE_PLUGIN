# pyright: reportPrivateUsage=false
"""Implementation of business node A3.40."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import EvidenceBundle
from production_optimizer.contracts.a3 import (
    AnalyzerObservationBranch,
    EvidenceCatalog,
    FindingDraftSet,
    PrioritizedSignalSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _build_finding_context,
    _evidence_supports_claim,
    _generate_finding_drafts,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _seal,
)


def handle_a3_40_give_approved_generator_bounded_catalogue_context_require(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("A3.40 requires ModelProviderPort wired into NodePorts")

    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )
    catalog = _read_model(ports, state, _require_ref(state, "EvidenceCatalog"), EvidenceCatalog)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    branches = [
        _read_model(
            ports,
            state,
            _require_stage_ref(state, node_id, "AnalyzerObservationBranch"),
            AnalyzerObservationBranch,
        )
        for node_id in ("A3.30", "A3.31", "A3.32", "A3.33")
    ]

    if not prioritized.signals:
        drafts, failures, tokens = [], [], 0
    else:
        context = _build_finding_context(prioritized, catalog, bundle, branches)
        known_evidence_ids = {entry.evidence_id for entry in catalog.entries}
        support_evidence_ids = {
            item.evidence_id for item in bundle.evidence if _evidence_supports_claim(item)
        }
        drafts, failures, tokens = _generate_finding_drafts(
            ports,
            state,
            context,
            known_evidence_ids=known_evidence_ids,
            support_evidence_ids=support_evidence_ids,
        )

    draft_set = _seal(
        FindingDraftSet(
            **_base_envelope(
                state,
                "FindingDraftSet",
                parents=[prioritized.content_digest, catalog.content_digest, bundle.content_digest],
            ),
            drafts=drafts,
            generation_failures=failures,
        )
    )
    ref = _put_envelope(ports, state, draft_set, node_id="A3.40")
    return NodeExecution(updates={"artifact_refs": [ref], "a3_model_tokens_spent": tokens})


__all__ = ["handle_a3_40_give_approved_generator_bounded_catalogue_context_require"]
