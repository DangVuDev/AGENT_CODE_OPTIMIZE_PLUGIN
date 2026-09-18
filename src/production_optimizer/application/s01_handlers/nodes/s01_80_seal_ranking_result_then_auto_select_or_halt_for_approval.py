# pyright: reportPrivateUsage=false
"""Implementation of business node S01.80."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.s01 import RankingResult, SelectionApproval, StrategyScore
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _POLICY_VERSION,
    _now,
    _pass_stage_id,
    _put_envelope,
    _require_ref,
    _required_state_str,
    _s01_pass_number,
    _seal,
    _stage_envelope,
)


def handle_s01_80_seal_ranking_result_then_auto_select_or_halt_for_approval(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Seal `RankingResult` (so a halt's `InterruptEnvelope.artifact_digest`
    references something real), then either auto-approve or halt for a real,
    resumable human decision.

    BR-01-005: REVERT (S06.80) sends the graph back to S01 with one more
    entry in `s01_excluded_strategy_ids` -- a real second pass through S01
    within the same case. `RankingResult`'s `artifact_id` is pass-scoped
    (mirrors `a3_handlers`'s pass-scoping) so that second pass's genuinely
    different content (the reverted strategy is now excluded) does not
    collide, under the default `_base_envelope` case-scoped `artifact_id`,
    with the first pass's already-recorded `RankingResult` --
    `merge_artifact_refs` (`contracts/state.py`) raises on exactly that
    same-key, different-digest collision.
    """

    portfolio_ref = _require_ref(state, "SolutionPortfolio")
    scores = cast("list[StrategyScore]", state.get("s01_scores") or [])
    ranked_ids = cast("list[str]", state.get("s01_ranked_strategy_ids") or [])
    sensitivity_flags = cast("list[str]", state.get("s01_sensitivity_flags") or [])
    pass_number = _s01_pass_number(state)
    ranking = _seal(
        RankingResult(
            **_stage_envelope(
                state, _pass_stage_id("S01.80", pass_number), "RankingResult", parents=[]
            ),
            solution_portfolio_digest=portfolio_ref.content_digest,
            scores=scores,
            ranked_strategy_ids=ranked_ids,
            sensitivity_flags=sensitivity_flags,
        )
    )
    ranking_ref = _put_envelope(ports, state, ranking, node_id="S01.80")

    routing = cast("dict[str, Any]", state.get("s01_routing_decision") or {})
    if not routing.get("needs_approval"):
        approval = SelectionApproval(decision="auto_selected", policy_version=_POLICY_VERSION)
        return NodeExecution(updates={"artifact_refs": [ranking_ref], "s01_approval": approval})

    resume_command = state.get("resume_command")
    if resume_command is None:
        interrupt = InterruptEnvelope(
            interrupt_id=f"{_required_state_str(state, 'case_id')}-S01-SELECTION",
            case_id=_required_state_str(state, "case_id"),
            thread_id=_required_state_str(state, "thread_id"),
            stage="S01.80",
            artifact_digest=ranking.content_digest,
            allowed_decisions=["approve", "reject"],
            required_actor_role="owner",
            policy_version=_POLICY_VERSION,
            issued_at=_now(),
            expires_at=_now() + timedelta(hours=24),
        )
        approval = SelectionApproval(decision="pending", policy_version=_POLICY_VERSION)
        return NodeExecution(
            updates={
                "artifact_refs": [ranking_ref],
                "pending_interrupt": interrupt,
                "s01_approval": approval,
            },
        )

    if resume_command.decision == "approve":
        approval = SelectionApproval(
            decision="approved",
            actor_id=resume_command.actor_id,
            actor_role=sorted(resume_command.actor_roles)[0],
            policy_version=resume_command.policy_version,
        )
        return NodeExecution(updates={"artifact_refs": [ranking_ref], "s01_approval": approval})

    approval = SelectionApproval(
        decision="rejected",
        actor_id=resume_command.actor_id,
        actor_role=sorted(resume_command.actor_roles)[0],
        policy_version=resume_command.policy_version,
    )
    return NodeExecution(
        route=NodeRoute.REJECTED,
        updates={"artifact_refs": [ranking_ref], "s01_approval": approval},
    )


__all__ = ["handle_s01_80_seal_ranking_result_then_auto_select_or_halt_for_approval"]
