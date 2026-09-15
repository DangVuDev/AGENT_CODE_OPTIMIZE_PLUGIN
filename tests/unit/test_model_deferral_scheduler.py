from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from production_optimizer.adapters.production import DeferredModelCallError
from production_optimizer.application.model_deferral_scheduler import (
    ModelDeferralScheduler,
    ModelDeferralSchedulerError,
)
from production_optimizer.contracts.platform import (
    DeferredModelCallRecord,
    DeferredModelCallStatus,
    ModelCallFailureRecord,
    ModelRole,
)


class _Deferrals:
    def __init__(self, records: list[DeferredModelCallRecord]) -> None:
        self.records = records
        self.succeeded: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.claim_args: dict[str, Any] | None = None

    def claim_due(
        self, *, tenant_id: str, lease_owner: str, lease_seconds: int, limit: int
    ) -> list[DeferredModelCallRecord]:
        self.claim_args = {
            "tenant_id": tenant_id,
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
            "limit": limit,
        }
        return self.records[:limit]

    def mark_succeeded(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord:
        del tenant_id
        self.succeeded.append(deferral_id)
        return self.records[0]

    def mark_failed(
        self, *, tenant_id: str, deferral_id: str, error_ref: str
    ) -> DeferredModelCallRecord:
        del tenant_id
        self.failed.append((deferral_id, error_ref))
        return self.records[0]

    def defer(self, record: DeferredModelCallRecord) -> DeferredModelCallRecord:
        self.records.append(record)
        return record

    def get(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord | None:
        del tenant_id
        return next((record for record in self.records if record.deferral_id == deferral_id), None)

    def healthcheck(self) -> bool:
        return True


class _Graph:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def invoke(self, state: Any, *, config: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((state, config))
        if self.error is not None:
            raise self.error
        return {"ok": True}


def _record(
    *, thread_id: str | None = "THREAD-1", node_id: str = "A3.40"
) -> DeferredModelCallRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return DeferredModelCallRecord(
        tenant_id="TENANT-1",
        deferral_id="model-call-1",
        case_id="CASE-1",
        thread_id=thread_id,
        node_id=node_id,
        idempotency_key="CASE-1:A3.40:v1",
        request_digest=f"sha256:{'a' * 64}",
        status=DeferredModelCallStatus.SCHEDULED,
        role=ModelRole.GENERATOR,
        prompt_version="v1",
        primary_model_id="model-1",
        failures=[
            ModelCallFailureRecord(
                provider_name="primary",
                model_id="model-1",
                retryable=True,
                error_type="ServerError",
                message="503",
            )
        ],
        retry_after_seconds=30,
        available_at=now,
        attempts=0,
        created_at=now,
        updated_at=now,
    )


def test_scheduler_resumes_checkpointed_graph_and_marks_succeeded() -> None:
    graph = _Graph()
    deferrals = _Deferrals([_record()])
    scheduler = ModelDeferralScheduler(
        deferrals=deferrals,
        graph_factories={"A3": lambda _record: graph},
        tenant_id="TENANT-1",
        lease_owner="worker-1",
    )

    results = scheduler.run_once(limit=5)

    assert results[0].status == "succeeded"
    assert deferrals.claim_args == {
        "tenant_id": "TENANT-1",
        "lease_owner": "worker-1",
        "lease_seconds": 300,
        "limit": 5,
    }
    assert graph.calls == [(None, {"configurable": {"thread_id": "THREAD-1"}})]
    assert deferrals.succeeded == ["model-call-1"]
    assert deferrals.failed == []


def test_scheduler_marks_missing_thread_id_failed() -> None:
    deferrals = _Deferrals([_record(thread_id=None)])
    scheduler = ModelDeferralScheduler(
        deferrals=deferrals,
        graph_factories={"A3": lambda _record: _Graph()},
        tenant_id="TENANT-1",
        lease_owner="worker-1",
    )

    results = scheduler.run_once()

    assert results[0].status == "failed"
    assert deferrals.failed == [("model-call-1", "model-deferral:missing-thread-id")]


def test_scheduler_leaves_record_deferred_when_provider_is_still_unavailable() -> None:
    graph = _Graph(
        error=DeferredModelCallError(
            request_id="CASE-1:A3.40:v1",
            failures=[],
            retry_after_seconds=30,
            deferral_id="model-call-1",
        )
    )
    deferrals = _Deferrals([_record()])
    scheduler = ModelDeferralScheduler(
        deferrals=deferrals,
        graph_factories={"A3": lambda _record: graph},
        tenant_id="TENANT-1",
        lease_owner="worker-1",
    )

    results = scheduler.run_once()

    assert results[0].status == "deferred"
    assert deferrals.succeeded == []
    assert deferrals.failed == []


def test_scheduler_rejects_unregistered_stage() -> None:
    deferrals = _Deferrals([_record(node_id="Z9.10")])
    scheduler = ModelDeferralScheduler(
        deferrals=deferrals,
        graph_factories={"A3": lambda _record: _Graph()},
        tenant_id="TENANT-1",
        lease_owner="worker-1",
    )

    with pytest.raises(ModelDeferralSchedulerError):
        scheduler.run_once()
