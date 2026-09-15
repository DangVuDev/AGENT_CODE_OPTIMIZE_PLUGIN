# pyright: reportPrivateUsage=false
"""Implementation of business node A3.80."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import (
    RiskAssessment,
    SolutionStrategy,
    SolutionStrategySet,
    StrategyDraftSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _RISK_SENSITIVE_KEYWORDS,
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
)


def handle_a3_80_determine_risk_blast_radius_reversibility_uncertainty_migration(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.70", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)

    strategies: list[SolutionStrategy] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)

        missing = [
            name
            for name, value in (
                ("impact_assessment", draft.impact_assessment),
                ("tradeoff_analysis", draft.tradeoff_analysis),
                ("validation_plan", draft.validation_plan),
                ("rollback_plan", draft.rollback_plan),
                ("scope_resolution", draft.scope_resolution),
            )
            if value is None
        ]
        if missing:
            # Should not happen given the pipeline's own sequencing (every
            # prior stage unconditionally fills its assigned field); this is
            # a defensive guard against a stage being skipped, not a normal
            # code path. A SolutionStrategy cannot be constructed without
            # every required nested field, so such a draft is dropped rather
            # than fabricated.
            continue

        assert draft.scope_resolution is not None
        assert draft.tradeoff_analysis is not None
        assert draft.rollback_plan is not None
        assert draft.impact_assessment is not None
        assert draft.validation_plan is not None

        risk_assessment = draft.risk_assessment
        if risk_assessment is None:
            paths = [entry.path_or_symbol for entry in draft.scope_resolution.entries]
            sensitive = any(
                keyword in path.lower() for path in paths for keyword in _RISK_SENSITIVE_KEYWORDS
            )
            risk_assessment = RiskAssessment(
                assessment_id=f"risk-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                risk_tier=draft.risk_ceiling,
                blast_radius=f"{len(paths)} path(s): {paths}" if paths else "unspecified scope",
                reversibility="fast" if draft.rollback_plan.reversible else "slow",
                uncertainty=draft.tradeoff_analysis.uncertainty,
                migration_impact=sensitive,
                security_impact=sensitive,
                factors=["scope touches a sensitive path"] if sensitive else [],
            )

        if not draft.scope_resolution.fully_resolved:
            reasons.append("scope_resolution is not fully resolved")

        eligible = draft.eligible and not reasons

        strategies.append(
            SolutionStrategy(
                strategy_id=draft.strategy_id,
                finding_ids=draft.finding_ids,
                title=draft.title,
                mechanism=draft.mechanism,
                strategy_tradeoffs=draft.strategy_tradeoffs,
                phase_templates=draft.phase_templates,
                risk_ceiling=draft.risk_ceiling,
                risk_assessment=risk_assessment,
                impact_assessment=draft.impact_assessment,
                tradeoff_analysis=draft.tradeoff_analysis,
                validation_plan=draft.validation_plan,
                rollback_plan=draft.rollback_plan,
                scope_resolution=draft.scope_resolution,
                evidence_ids=draft.evidence_ids,
                assumptions=draft.assumptions,
                eligible=eligible,
                gate_reasons=reasons,
            )
        )

    strategy_set = _seal(
        SolutionStrategySet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.80", current_pass),
                "SolutionStrategySet",
                parents=[draft_set.content_digest],
            ),
            strategies=strategies,
        )
    )
    ref = _put_envelope(ports, state, strategy_set, node_id="A3.80")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_80_determine_risk_blast_radius_reversibility_uncertainty_migration"]
