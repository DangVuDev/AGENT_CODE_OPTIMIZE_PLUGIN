# pyright: reportPrivateUsage=false
"""Implementation of business node A3.82."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    RevisionDirective,
    SolutionStrategySet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _MAX_REVISION_ATTEMPTS,
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
)


def handle_a3_82_revise_failed_dimensions_preserve_attempts_enforce_retry(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Targets only rejected strategies/findings for regeneration.

    Accepted artifacts are reused unchanged on the next pass, per the
    playbook ("A3.82 may target only rejected findings/strategies").
    """

    current_pass = _revision_pass(state)
    strategy_ref = _require_stage_ref(
        state, _pass_stage_id("A3.80", current_pass), "SolutionStrategySet"
    )
    strategy_set = _read_model(ports, state, strategy_ref, SolutionStrategySet)
    quality_ref = _require_stage_ref(
        state, _pass_stage_id("A3.81", current_pass), "A3QualityReport"
    )
    quality = _read_model(ports, state, quality_ref, A3QualityReport)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )

    next_attempt = current_pass + 1
    tokens_so_far = state.get("a3_model_tokens_spent", 0)
    budget_exhausted = (
        next_attempt >= _MAX_REVISION_ATTEMPTS
        or tokens_so_far >= request.budget.maximum_model_tokens
    )

    targeted_strategy_ids = [s.strategy_id for s in strategy_set.strategies if not s.eligible]
    targeted_finding_ids = sorted(
        {
            finding_id
            for s in strategy_set.strategies
            if not s.eligible
            for finding_id in s.finding_ids
        }
    )

    if budget_exhausted or not targeted_strategy_ids:
        reason = (
            "revision/token budget exhausted"
            if budget_exhausted
            else "quality gate failed with no targetable ineligible strategy"
        )
        directive = _seal(
            RevisionDirective(
                **_stage_envelope(
                    state,
                    _pass_stage_id("A3.82", current_pass),
                    "RevisionDirective",
                    parents=[quality.content_digest],
                ),
                attempt_number=next_attempt,
                targeted_finding_ids=[],
                targeted_strategy_ids=[],
                reason=reason,
            )
        )
        ref = _put_envelope(ports, state, directive, node_id="A3.82")
        return NodeExecution(
            route=NodeRoute.REJECTED, updates={"artifact_refs": [ref], "a3_revision_attempts": 1}
        )

    directive = _seal(
        RevisionDirective(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.82", current_pass),
                "RevisionDirective",
                parents=[quality.content_digest],
            ),
            attempt_number=next_attempt,
            targeted_finding_ids=targeted_finding_ids,
            targeted_strategy_ids=targeted_strategy_ids,
            reason="targeted revision of ineligible strategies",
        )
    )
    ref = _put_envelope(ports, state, directive, node_id="A3.82")
    return NodeExecution(
        route=NodeRoute.CONTINUE, updates={"artifact_refs": [ref], "a3_revision_attempts": 1}
    )


__all__ = ["handle_a3_82_revise_failed_dimensions_preserve_attempts_enforce_retry"]
