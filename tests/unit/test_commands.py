from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from production_optimizer.contracts import ArtifactRef, StartCommandType, StartWorkflowCommand


def _payload_ref() -> ArtifactRef:
    return ArtifactRef(
        artifact_type="CommandPayload",
        schema_version="1.0",
        artifact_id="CMD-PAYLOAD-1",
        content_digest=f"sha256:{'a' * 64}",
        uri="s3://tenant/CMD-PAYLOAD-1",
    )


def test_start_command_builds_compact_root_state() -> None:
    issued_at = datetime.now(UTC)
    command = StartWorkflowCommand(
        command_id="CMD-1",
        command_type=StartCommandType.START_QUALIFIED_CASE,
        tenant_id="TENANT-1",
        case_id="OPT-1",
        thread_id="THREAD-1",
        actor_id="outbox-dispatcher",
        idempotency_key="qualified:sha256:abc",
        payload_ref=_payload_ref(),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
    )
    state = command.initial_state()
    assert state.get("entrypoint") == "qualified"
    assert state.get("lane") == "automatic"
    assert state.get("artifact_refs") == [_payload_ref()]


def test_start_command_rejects_invalid_lifetime() -> None:
    issued_at = datetime.now(UTC)
    with pytest.raises(ValidationError, match="expiration must follow"):
        StartWorkflowCommand(
            command_id="CMD-1",
            command_type=StartCommandType.CREATE_MANUAL_CASE,
            tenant_id="TENANT-1",
            case_id="OPT-1",
            thread_id="THREAD-1",
            actor_id="user-1",
            idempotency_key="manual:sha256:abc",
            payload_ref=_payload_ref(),
            issued_at=issued_at,
            expires_at=issued_at,
        )
