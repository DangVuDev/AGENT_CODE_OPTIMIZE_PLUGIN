from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from production_optimizer.adapters.production.postgres_case_repository import require_field
from production_optimizer.adapters.production.postgres_intent_ledger import (
    artifact_ref_from_row,
    intent_record_from_row,
)
from production_optimizer.adapters.production.postgres_outbox import outbox_record_from_row
from production_optimizer.contracts.platform import IntentStatus
from production_optimizer.contracts.state import OptimizationState


def _artifact_columns(*, digest_character: str = "a") -> dict[str, Any]:
    return {
        "output_artifact_type": "SolutionPortfolio",
        "output_schema_version": "1.0",
        "output_artifact_id": "ART-1",
        "output_digest": f"sha256:{digest_character * 64}",
        "output_uri": "s3://development/ART-1",
    }


def _intent_row(*, status: str = "pending", **output_columns: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "tenant_id": "tenant-1",
        "idempotency_key": "case-1:A1.10:1",
        "case_id": "case-1",
        "node_id": "A1.10",
        "input_digest": f"sha256:{'b' * 64}",
        "status": status,
        "output_artifact_type": None,
        "output_schema_version": None,
        "output_artifact_id": None,
        "output_digest": None,
        "output_uri": None,
        "updated_at": datetime.now(UTC),
    }
    row.update(output_columns)
    return row


def test_require_field_returns_present_string_value() -> None:
    state: OptimizationState = {"case_id": "OPT-1"}
    assert require_field(state, "case_id") == "OPT-1"


@pytest.mark.parametrize("state", [{}, {"case_id": ""}])
def test_require_field_rejects_missing_or_empty_value(state: OptimizationState) -> None:
    with pytest.raises(ValueError, match="missing required field 'case_id'"):
        require_field(state, "case_id")


def test_artifact_ref_from_row_reconstructs_full_ref() -> None:
    ref = artifact_ref_from_row(_artifact_columns())
    assert ref is not None
    assert ref.artifact_type == "SolutionPortfolio"
    assert ref.schema_version == "1.0"
    assert ref.artifact_id == "ART-1"
    assert ref.content_digest == f"sha256:{'a' * 64}"
    assert ref.uri == "s3://development/ART-1"


def test_artifact_ref_from_row_returns_none_when_any_field_missing() -> None:
    columns = _artifact_columns()
    columns["output_uri"] = None
    assert artifact_ref_from_row(columns) is None


def test_artifact_ref_from_row_returns_none_for_all_null_columns() -> None:
    columns = {key: None for key in _artifact_columns()}
    assert artifact_ref_from_row(columns) is None


def test_intent_record_from_row_without_output_ref() -> None:
    record = intent_record_from_row(_intent_row())
    assert record.status is IntentStatus.PENDING
    assert record.output_ref is None


def test_intent_record_from_row_with_output_ref() -> None:
    record = intent_record_from_row(_intent_row(status="completed", **_artifact_columns()))
    assert record.status is IntentStatus.COMPLETED
    assert record.output_ref is not None
    assert record.output_ref.artifact_id == "ART-1"


def test_outbox_record_from_row_stringifies_uuid_event_id() -> None:
    class _FakeUUID:
        def __str__(self) -> str:
            return "11111111-1111-1111-1111-111111111111"

    record = outbox_record_from_row(
        {
            "event_id": _FakeUUID(),
            "tenant_id": "tenant-1",
            "case_id": "case-1",
            "topic": "case.updated",
            "payload_ref": "s3://development/event-1",
            "payload_digest": f"sha256:{'c' * 64}",
            "available_at": datetime.now(UTC),
            "delivered_at": None,
            "attempts": 0,
            "last_error_ref": None,
        }
    )
    assert record.event_id == "11111111-1111-1111-1111-111111111111"
    assert isinstance(record.event_id, str)
