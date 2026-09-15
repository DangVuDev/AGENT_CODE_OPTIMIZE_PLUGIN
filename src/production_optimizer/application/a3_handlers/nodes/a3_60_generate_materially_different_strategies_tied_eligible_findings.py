# pyright: reportPrivateUsage=false
"""Implementation of business node A3.60."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import FindingSet, SolutionStrategySet, StrategyDraftSet
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _build_strategy_context,
    _generate_strategy_drafts,
    _latest_revision_directive,
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _solution_strategy_to_draft,
    _stage_envelope,
)


def handle_a3_60_generate_materially_different_strategies_tied_eligible_findings(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("A3.60 requires ModelProviderPort wired into NodePorts")

    finding_set = _read_model(ports, state, _require_ref(state, "FindingSet"), FindingSet)
    current_pass = _revision_pass(state)
    directive = _latest_revision_directive(ports, state)

    if current_pass > 0 and directive is not None and directive.targeted_strategy_ids:
        eligible_findings = [
            f for f in finding_set.findings if f.finding_id in directive.targeted_finding_ids
        ]
        previous_ref = _require_stage_ref(
            state, _pass_stage_id("A3.80", current_pass - 1), "SolutionStrategySet"
        )
        previous = _read_model(ports, state, previous_ref, SolutionStrategySet)
        carried_forward = [
            _solution_strategy_to_draft(strategy)
            for strategy in previous.strategies
            if strategy.strategy_id not in directive.targeted_strategy_ids
        ]
    else:
        eligible_findings = finding_set.findings
        carried_forward = []

    context = _build_strategy_context(eligible_findings)
    new_drafts, tokens = _generate_strategy_drafts(ports, state, context, attempt=current_pass)

    draft_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.60", current_pass),
                "StrategyDraftSet",
                parents=[finding_set.content_digest],
            ),
            strategies=[*carried_forward, *new_drafts],
        )
    )
    ref = _put_envelope(ports, state, draft_set, node_id="A3.60")
    return NodeExecution(updates={"artifact_refs": [ref], "a3_model_tokens_spent": tokens})


__all__ = ["handle_a3_60_generate_materially_different_strategies_tied_eligible_findings"]
