# pyright: reportPrivateUsage=false
"""Implementation of business node A3.61."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import StrategyDraft, StrategyDraftSet
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


def handle_a3_61_define_ordered_phases_exactly_one_logical_treatment(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.60", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        sequences = [phase.sequence for phase in draft.phase_templates]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            reasons.append("phase_templates sequence numbers are not strictly ordered/unique")
        eligible = draft.eligible and not reasons
        updated.append(draft.model_copy(update={"eligible": eligible, "gate_reasons": reasons}))

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.61", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.61")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_61_define_ordered_phases_exactly_one_logical_treatment"]
