from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from production_optimizer.adapters.production.migrations import apply_migrations
from production_optimizer.adapters.production.postgres_case_repository import (
    PostgresCaseRepository,
)
from production_optimizer.adapters.production.postgres_outbox import PostgresOutboxAdapter
from production_optimizer.contracts.platform import OutboxRecord


def _dsn() -> str:
    return os.environ.get(
        "OPTIMIZER_DATABASE_DSN", "postgresql://optimizer:change-me@localhost:5432/optimizer"
    )


def _redacted(dsn: str) -> str:
    parsed = urlsplit(dsn)
    host = parsed.hostname or "unknown-host"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{parsed.path}"


def _require_database(dsn: str) -> None:
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        pytest.skip(
            f"PostgreSQL is not reachable at {_redacted(dsn)}; "
            "start deploy/development/compose.yaml to run this suite"
        )


def _create_case(dsn: str, *, tenant_id: str, case_id: str) -> None:
    with PostgresCaseRepository(dsn) as repo:
        repo.create(
            {
                "case_id": case_id,
                "thread_id": f"thread-{uuid4()}",
                "tenant_id": tenant_id,
                "lane": "manual",
                "status": "open",
            }
        )


def _record(
    *,
    tenant_id: str,
    case_id: str,
    event_id: str | None = None,
    topic: str = "case.updated",
    available_at: datetime | None = None,
) -> OutboxRecord:
    return OutboxRecord(
        event_id=event_id or str(uuid4()),
        tenant_id=tenant_id,
        case_id=case_id,
        topic=topic,
        payload_ref=f"s3://development/{uuid4()}",
        payload_digest=f"sha256:{'a' * 64}",
        available_at=available_at or datetime.now(UTC),
        attempts=0,
    )


def test_publish_and_claim_round_trip() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)
    record = _record(tenant_id=tenant_id, case_id=case_id)

    with PostgresOutboxAdapter(dsn) as outbox:
        published = outbox.publish(record)
        claimed = outbox.claim(tenant_id=tenant_id, limit=10)

    assert published.event_id == record.event_id
    assert [item.event_id for item in claimed] == [record.event_id]


def test_claim_respects_available_at() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)
    future = datetime.now(UTC) + timedelta(hours=1)
    record = _record(tenant_id=tenant_id, case_id=case_id, available_at=future)

    with PostgresOutboxAdapter(dsn) as outbox:
        outbox.publish(record)
        claimed = outbox.claim(tenant_id=tenant_id, limit=10)

    assert claimed == []


def test_claim_respects_limit() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)

    with PostgresOutboxAdapter(dsn) as outbox:
        for _ in range(3):
            outbox.publish(_record(tenant_id=tenant_id, case_id=case_id))
        claimed = outbox.claim(tenant_id=tenant_id, limit=2)

    assert len(claimed) == 2


def test_mark_delivered_excludes_row_from_future_claims() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)
    record = _record(tenant_id=tenant_id, case_id=case_id)

    with PostgresOutboxAdapter(dsn) as outbox:
        outbox.publish(record)
        delivered = outbox.mark_delivered(tenant_id=tenant_id, event_id=record.event_id)
        claimed = outbox.claim(tenant_id=tenant_id, limit=10)

    assert delivered.delivered_at is not None
    assert claimed == []


def test_mark_failed_increments_attempts_and_is_visible_via_raw_query() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)
    record = _record(tenant_id=tenant_id, case_id=case_id)

    with PostgresOutboxAdapter(dsn) as outbox:
        outbox.publish(record)
        failed = outbox.mark_failed(
            tenant_id=tenant_id, event_id=record.event_id, error_ref="s3://development/error-1"
        )

    assert failed.attempts == 1
    assert failed.last_error_ref == "s3://development/error-1"

    with (
        psycopg.Connection[DictRow].connect(dsn, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "SELECT attempts, last_error_ref FROM optimizer_control.outbox "
            "WHERE event_id = %s::uuid",
            (record.event_id,),
        )
        row = cur.fetchone()

    assert row is not None
    assert row["attempts"] == 1
    assert row["last_error_ref"] == "s3://development/error-1"


def test_publish_twice_with_same_event_id_is_idempotent() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)
    record = _record(tenant_id=tenant_id, case_id=case_id)

    with PostgresOutboxAdapter(dsn) as outbox:
        first = outbox.publish(record)
        second = outbox.publish(record)

    assert first == second

    with (
        psycopg.Connection[DictRow].connect(dsn, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "SELECT count(*) AS n FROM optimizer_control.outbox WHERE event_id = %s::uuid",
            (record.event_id,),
        )
        row = cur.fetchone()

    assert row is not None
    assert row["n"] == 1
