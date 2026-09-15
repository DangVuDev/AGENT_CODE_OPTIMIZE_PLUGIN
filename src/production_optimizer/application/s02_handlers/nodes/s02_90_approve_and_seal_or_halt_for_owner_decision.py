# pyright: reportPrivateUsage=false
"""Implementation of business node S02.90."""

from __future__ import annotations

from datetime import timedelta
from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.s02 import PlanApproval
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _APPROVAL_RISK_TIERS,
    _POLICY_VERSION,
    _now,
    _read_required,
    _read_selected_solution,
    _require_ref,
    _required_state_str,
    _strategy_by_id,
)


def handle_s02_90_approve_and_seal_or_halt_for_owner_decision(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Approve and seal: the real artifacts are already sealed at S02.81 --
    this node is purely the approval gate, real and resumable like S01.80."""

    selected = _read_selected_solution(ports, state)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    plan_ref = _require_ref(state, "ExecutionPlan")

    if strategy.risk_ceiling not in _APPROVAL_RISK_TIERS:
        approval = PlanApproval(decision="auto_approved", policy_version=_POLICY_VERSION)
        return NodeExecution(updates={"s02_approval": approval})

    resume_command = state.get("resume_command")
    if resume_command is None:
        interrupt = InterruptEnvelope(
            interrupt_id=f"{_required_state_str(state, 'case_id')}-S02-PLAN",
            case_id=_required_state_str(state, "case_id"),
            thread_id=_required_state_str(state, "thread_id"),
            stage="S02.90",
            artifact_digest=plan_ref.content_digest,
            allowed_decisions=["approve", "reject"],
            required_actor_role="owner",
            policy_version=_POLICY_VERSION,
            issued_at=_now(),
            expires_at=_now() + timedelta(hours=24),
        )
        approval = PlanApproval(decision="pending", policy_version=_POLICY_VERSION)
        return NodeExecution(
            route=NodeRoute.APPROVAL,
            updates={"pending_interrupt": interrupt, "s02_approval": approval},
        )

    if resume_command.decision == "approve":
        approval = PlanApproval(
            decision="approved",
            actor_id=resume_command.actor_id,
            actor_role=sorted(resume_command.actor_roles)[0],
            policy_version=resume_command.policy_version,
        )
        return NodeExecution(updates={"s02_approval": approval})

    approval = PlanApproval(
        decision="rejected",
        actor_id=resume_command.actor_id,
        actor_role=sorted(resume_command.actor_roles)[0],
        policy_version=resume_command.policy_version,
    )
    return NodeExecution(route=NodeRoute.REJECTED, updates={"s02_approval": approval})


__all__ = ["handle_s02_90_approve_and_seal_or_halt_for_owner_decision"]
