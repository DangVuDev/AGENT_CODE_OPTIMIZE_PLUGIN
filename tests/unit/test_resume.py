from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from production_optimizer.application.resume import ResumeAuthorizationError, authorize_resume
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import ActorContext

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = f"sha256:{'a' * 64}"


def _values() -> tuple[ResumeInterruptCommand, InterruptEnvelope, ActorContext]:
    command = ResumeInterruptCommand(
        command_id="cmd-1",
        tenant_id="tenant-1",
        case_id="case-1",
        thread_id="thread-1",
        interrupt_id="interrupt-1",
        actor_id="actor-1",
        actor_roles={"owner"},
        decision="approve",
        artifact_digest=DIGEST,
        policy_version="policy-1",
        issued_at=NOW,
    )
    interrupt = InterruptEnvelope(
        interrupt_id="interrupt-1",
        case_id="case-1",
        thread_id="thread-1",
        stage="A1.90",
        artifact_digest=DIGEST,
        allowed_decisions=["approve", "reject"],
        required_actor_role="owner",
        policy_version="policy-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    actor = ActorContext(
        actor_id="actor-1",
        tenant_id="tenant-1",
        roles={"owner"},
        authenticated_at=NOW,
    )
    return command, interrupt, actor


def test_resume_authorization_accepts_fully_bound_command() -> None:
    command, interrupt, actor = _values()
    authorize_resume(command=command, interrupt=interrupt, actor=actor, now=NOW)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("tenant_id", "other", "tenant mismatch"),
        ("artifact_digest", f"sha256:{'b' * 64}", "artifact digest mismatch"),
        ("policy_version", "other", "policy version mismatch"),
        ("decision", "revise", "decision is not allowed"),
    ],
)
def test_resume_authorization_rejects_unbound_commands(
    field: str, value: str, reason: str
) -> None:
    command, interrupt, actor = _values()
    command = command.model_copy(update={field: value})
    with pytest.raises(ResumeAuthorizationError, match=reason):
        authorize_resume(command=command, interrupt=interrupt, actor=actor, now=NOW)


def test_resume_authorization_rejects_expired_interrupt() -> None:
    command, interrupt, actor = _values()
    with pytest.raises(ResumeAuthorizationError, match="expired"):
        authorize_resume(
            command=command, interrupt=interrupt, actor=actor, now=NOW + timedelta(hours=2)
        )
