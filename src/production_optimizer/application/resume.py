from __future__ import annotations

from datetime import datetime

from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import ActorContext


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
