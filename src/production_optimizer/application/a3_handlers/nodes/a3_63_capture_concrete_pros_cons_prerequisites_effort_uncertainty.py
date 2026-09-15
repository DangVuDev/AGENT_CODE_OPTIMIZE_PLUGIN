# pyright: reportPrivateUsage=false
"""Implementation of business node A3.63."""

from __future__ import annotations

from typing import Literal

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import StrategyDraft, StrategyDraftSet, TradeoffAnalysis
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
)


def handle_a3_63_capture_concrete_pros_cons_prerequisites_effort_uncertainty(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Deterministic tradeoff assembly + duplicate-strategy detection.

    Same limitation as A3.62: no execution has happened, so this derives a
    coarse `TradeoffAnalysis` from what the generator already supplied
    (`strategy_tradeoffs` text, phase count) rather than a real cost/benefit
    analysis. The one thing genuinely enforced deterministically is the
    playbook's duplicate-strategy exit test.
    """

    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.62", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)

    seen_tradeoffs: dict[str, str] = {}
    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        duplicate_of = seen_tradeoffs.get(draft.strategy_tradeoffs)
        if duplicate_of is not None:
            reasons.append(f"strategy_tradeoffs text is identical to {duplicate_of}")
        else:
            seen_tradeoffs[draft.strategy_tradeoffs] = draft.strategy_id

        tradeoff_analysis = draft.tradeoff_analysis
        if tradeoff_analysis is None:
            phase_count = len(draft.phase_templates)
            effort: Literal["low", "medium", "high"] = (
                "low" if phase_count <= 1 else "medium" if phase_count <= 3 else "high"
            )
            tradeoff_analysis = TradeoffAnalysis(
                analysis_id=f"tradeoff-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                pros=[draft.strategy_tradeoffs],
                cons=[f"unvalidated until executed: {draft.mechanism[:200]}"],
                prerequisites=[
                    p.phase_id for p in draft.phase_templates if p.phase_kind == "diagnostic"
                ],
                effort=effort,
                uncertainty=0.5,
                evidence_ids=draft.evidence_ids,
            )

        eligible = draft.eligible and not reasons
        updated.append(
            draft.model_copy(
                update={
                    "gate_reasons": reasons,
                    "eligible": eligible,
                    "tradeoff_analysis": tradeoff_analysis,
                }
            )
        )

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.63", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.63")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_63_capture_concrete_pros_cons_prerequisites_effort_uncertainty"]
