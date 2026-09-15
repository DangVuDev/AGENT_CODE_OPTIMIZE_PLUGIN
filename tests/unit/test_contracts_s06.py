from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.s06 import RepositoryApplyResult


def _digest(character: str = "a") -> str:
    return f"sha256:{character * 64}"


def _producer() -> ProducerIdentity:
    return ProducerIdentity(name="s06-service", version="1.0.0")


def _at() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _apply_result_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "artifact_id": "ART-RAR-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _at(),
        "producer": _producer(),
        "content_digest": _digest("b"),
        "phase_id": "phase-1",
        "patch_digest": _digest("c"),
        "applied": True,
        "branch_name": "optimizer/OPT-1-phase-1",
        "commit_sha": "a" * 40,
        "base_revision": "b" * 40,
        "failure_reason": None,
        "approval_decision": "approved",
        "approval_actor_id": "owner-1",
        "policy_version": "s06-decision-v1",
    }
    kwargs.update(overrides)
    return kwargs


def test_repository_apply_result_construction_and_pinned_fields() -> None:
    result = RepositoryApplyResult(**_apply_result_kwargs())
    assert result.artifact_type == "RepositoryApplyResult"
    assert result.schema_version == "1.0"
    assert result.applied is True
    assert result.branch_name == "optimizer/OPT-1-phase-1"


def test_repository_apply_result_requires_commit_identity_when_applied() -> None:
    kwargs = _apply_result_kwargs(branch_name=None)
    with pytest.raises(ValidationError, match="applied=True requires both"):
        RepositoryApplyResult(**kwargs)

    kwargs = _apply_result_kwargs(commit_sha=None)
    with pytest.raises(ValidationError, match="applied=True requires both"):
        RepositoryApplyResult(**kwargs)


def test_repository_apply_result_requires_failure_reason_when_not_applied() -> None:
    kwargs = _apply_result_kwargs(
        applied=False, branch_name=None, commit_sha=None, failure_reason=None
    )
    with pytest.raises(ValidationError, match="applied=False requires a failure_reason"):
        RepositoryApplyResult(**kwargs)


def test_repository_apply_result_records_a_real_failure_without_commit_identity() -> None:
    kwargs = _apply_result_kwargs(
        applied=False,
        branch_name=None,
        commit_sha=None,
        failure_reason="real repository HEAD no longer matches base_revision",
    )
    result = RepositoryApplyResult(**kwargs)
    assert result.applied is False
    assert result.branch_name is None
    assert result.commit_sha is None
