# pyright: reportPrivateUsage=false
"""Implementation of business node A3.62."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a3 import (
    CriterionImpact,
    ImpactAssessment,
    StrategyDraft,
    StrategyDraftSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
)


def handle_a3_62_assess_every_criterion_guardrail_label_measured_versus(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Assigns a conservative `forecast` impact for every criterion.

    A3 never executes a strategy (non-negotiable: A3 does not edit source),
    so no criterion impact can honestly be `basis="measured"` at this stage.
    A real forecast (`direction`, `confidence`) needs either a richer
    generator schema or actual execution telemetry, neither of which exists
    yet — this is a deliberately conservative placeholder, not a claim of
    analysis depth this platform doesn't have.
    """

    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.61", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        if draft.impact_assessment is not None:
            updated.append(draft)
            continue
        impacts = [
            CriterionImpact(
                criterion_id=criterion.criterion_id,
                direction="unknown",
                confidence=0.0,
                basis="forecast",
            )
            for criterion in request.criteria
        ]
        assessment = ImpactAssessment(
            assessment_id=f"impact-{draft.strategy_id}",
            strategy_id=draft.strategy_id,
            criterion_impacts=impacts,
        )
        updated.append(draft.model_copy(update={"impact_assessment": assessment}))

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.62", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.62")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_62_assess_every_criterion_guardrail_label_measured_versus"]
