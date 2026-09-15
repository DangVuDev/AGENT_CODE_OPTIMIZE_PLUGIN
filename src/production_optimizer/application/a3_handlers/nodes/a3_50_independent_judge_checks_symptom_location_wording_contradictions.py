# pyright: reportPrivateUsage=false
"""Implementation of business node A3.50."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import (
    CitationResolutionReportSet,
    FindingDraftSet,
    FindingJudgement,
    FindingJudgementSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _build_judge_context,
    _judge_finding,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a3_50_independent_judge_checks_symptom_location_wording_contradictions(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("A3.50 requires ModelProviderPort wired into NodePorts")

    draft_set = _read_model(ports, state, _require_ref(state, "FindingDraftSet"), FindingDraftSet)
    citations = _read_model(
        ports,
        state,
        _require_ref(state, "CitationResolutionReportSet"),
        CitationResolutionReportSet,
    )
    citations_by_finding = {r.finding_id: r for r in citations.reports}

    judgements: list[FindingJudgement] = []
    tokens_spent = 0
    for draft in draft_set.drafts:
        citation = citations_by_finding.get(draft.finding_id)
        context = _build_judge_context(draft, citation)
        judgement, spent = _judge_finding(ports, state, draft.finding_id, context)
        tokens_spent += spent
        if judgement is not None:
            judgements.append(judgement)

    judgement_set = _seal(
        FindingJudgementSet(
            **_base_envelope(
                state,
                "FindingJudgementSet",
                parents=[draft_set.content_digest, citations.content_digest],
            ),
            judgements=judgements,
        )
    )
    ref = _put_envelope(ports, state, judgement_set, node_id="A3.50")
    return NodeExecution(updates={"artifact_refs": [ref], "a3_model_tokens_spent": tokens_spent})


__all__ = ["handle_a3_50_independent_judge_checks_symptom_location_wording_contradictions"]
