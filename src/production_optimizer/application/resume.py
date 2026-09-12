from __future__ import annotations

from datetime import datetime
from typing import Any

from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.contracts.state import OptimizationState


class ResumeAuthorizationError(PermissionError):
    pass


def authorize_resume(
    *,
    command: ResumeInterruptCommand,
    interrupt: InterruptEnvelope,
    actor: ActorContext,
    now: datetime,
) -> None:
    """Fail closed unless a resume is identity-, tenant-, digest- and policy-bound."""

    if now.tzinfo is None:
        raise ValueError("resume authorization time must be timezone-aware")
    checks = {
        "expired interrupt": now >= interrupt.expires_at,
        "actor identity mismatch": command.actor_id != actor.actor_id,
        "tenant mismatch": command.tenant_id != actor.tenant_id,
        "case mismatch": command.case_id != interrupt.case_id,
        "thread mismatch": command.thread_id != interrupt.thread_id,
        "interrupt mismatch": command.interrupt_id != interrupt.interrupt_id,
        "artifact digest mismatch": command.artifact_digest != interrupt.artifact_digest,
        "policy version mismatch": command.policy_version != interrupt.policy_version,
        "decision is not allowed": command.decision not in interrupt.allowed_decisions,
        "required actor role is missing": interrupt.required_actor_role not in actor.roles,
        "asserted roles exceed authenticated roles": not command.actor_roles <= actor.roles,
    }
    failures = [reason for reason, failed in checks.items() if failed]
    if failures:
        raise ResumeAuthorizationError("; ".join(failures))


def resume_case(
    *,
    graph: Any,
    state: OptimizationState,
    command: ResumeInterruptCommand,
    actor: ActorContext,
    now: datetime,
) -> dict[str, Any]:
    """Resume a compiled graph run that previously halted on `pending_interrupt`.

    The halted node's own intent is already COMPLETED (it recorded the halt
    itself), so re-invoking `graph` unchanged would just replay that same
    halt forever -- every node up to and including it is idempotent-cached.
    This authorizes the resume, then re-invokes from `START` with
    `resume_command` set (the halted node reads it to pick a different route
    this time) and `resume_attempts` incremented (gives that one re-execution
    a distinct idempotency key -- see `NodeRuntime._derive_idempotency_key`
    -- so it actually re-runs instead of hitting the cache). Every node
    already completed before the halt still cache-hits unchanged; every node
    after the (now-resolved) halt runs for the first time, normally.
    """

    interrupt = state.get("pending_interrupt")
    if interrupt is None:
        raise ValueError("state has no pending_interrupt to resume")
    authorize_resume(command=command, interrupt=interrupt, actor=actor, now=now)

    # `artifact_refs`/`node_routes` use conflict-detecting reducers
    # (`merge_artifact_refs`/`merge_node_routes`): the halted node is about
    # to legitimately produce a *different* value (a new route; a
    # same-artifact_id-but-different-content artifact, since its decision
    # genuinely changed) than the one it produced when it halted, which
    # those reducers correctly treat as a hard conflict, not a resume. Only
    # that one node's prior entries need to go -- `interrupt.artifact_digest`
    # is exactly the content_digest the halted node sealed when it recorded
    # this interrupt (every halting node sets it that way), so it uniquely
    # identifies which `artifact_refs` entry is stale without resume.py
    # needing a node_id -> artifact_type lookup. Every other node's
    # (including the original seed input's) refs are untouched.
    artifact_refs = [
        ref
        for ref in state.get("artifact_refs", [])
        if ref.content_digest != interrupt.artifact_digest
    ]
    node_routes = dict(state.get("node_routes", {}))
    node_routes.pop(interrupt.stage, None)

    resumed_state: OptimizationState = {
        **state,
        "artifact_refs": artifact_refs,
        "node_routes": node_routes,
        "resume_command": command,
        "resume_attempts": state.get("resume_attempts", 0) + 1,
        "resume_target_node": interrupt.stage,
        "pending_interrupt": None,
    }
    return graph.invoke(resumed_state)
