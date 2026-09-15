# pyright: reportPrivateUsage=false
"""Implementation of business node A3.51."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a2 import EvidenceBundle, TrustLevel
from production_optimizer.contracts.a3 import (
    AnalyzerObservationBranch,
    CitationResolutionReportSet,
    Finding,
    FindingDraftSet,
    FindingJudgementSet,
    FindingSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _compute_trust_level,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _seal,
)


def handle_a3_51_assign_observation_hypothesis_verified_cause_maturity_never(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    draft_set = _read_model(ports, state, _require_ref(state, "FindingDraftSet"), FindingDraftSet)
    citations = _read_model(
        ports,
        state,
        _require_ref(state, "CitationResolutionReportSet"),
        CitationResolutionReportSet,
    )
    judgements = _read_model(
        ports, state, _require_ref(state, "FindingJudgementSet"), FindingJudgementSet
    )
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

    citations_by_finding = {r.finding_id: r for r in citations.reports}
    judgement_by_finding = {j.finding_id: j for j in judgements.judgements}

    findings: list[Finding] = []
    for draft in draft_set.drafts:
        judgement = judgement_by_finding.get(draft.finding_id)
        citation = citations_by_finding.get(draft.finding_id)
        if judgement is None or citation is None or not citation.all_resolved:
            continue

        trust_level = _compute_trust_level(draft, citation, judgement, bundle)
        claim_type = draft.claim_type
        if claim_type == "verified_cause" and (
            judgement.verdict != "accept" or trust_level != TrustLevel.T4
        ):
            claim_type = "hypothesis"

        findings.append(
            Finding(
                finding_id=draft.finding_id,
                problem_signal_ids=draft.problem_signal_ids,
                claim_type=claim_type,
                symptom=draft.symptom,
                scope_files=draft.scope_files,
                scope_symbols=draft.scope_symbols,
                runtime_path=draft.runtime_path,
                causal_claim=draft.causal_claim,
                supporting_evidence_ids=draft.supporting_evidence_ids,
                counterevidence_ids=draft.counterevidence_ids,
                analyzer_coverage=draft.analyzer_coverage,
                confidence=draft.confidence,
                trust_level=trust_level,
                judgement=judgement,
                unknowns=draft.unknowns,
            )
        )

    if not findings:
        # FindingSet requires >=1 finding (contracts/a3.py), so there is no
        # artifact to seal here -- an honest rejected route out, not a crash.
        # A weak/local model producing zero "accept" judgements with fully
        # resolved citations is expected, not exceptional (e.g. small local
        # Ollama models under-perform on this task).
        return NodeExecution(route=NodeRoute.REJECTED, updates={})

    all_observations = [obs for branch in branches for obs in branch.observations]
    coverage_gaps = [gap for branch in branches for gap in branch.coverage_gaps]

    finding_set = _seal(
        FindingSet(
            **_base_envelope(
                state,
                "FindingSet",
                parents=[
                    bundle.content_digest,
                    draft_set.content_digest,
                    judgements.content_digest,
                ],
            ),
            evidence_bundle_digest=bundle.content_digest,
            findings=findings,
            observations=all_observations,
            citation_reports=citations.reports,
            coverage_gaps=coverage_gaps,
        )
    )
    ref = _put_envelope(ports, state, finding_set, node_id="A3.51")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_51_assign_observation_hypothesis_verified_cause_maturity_never"]
