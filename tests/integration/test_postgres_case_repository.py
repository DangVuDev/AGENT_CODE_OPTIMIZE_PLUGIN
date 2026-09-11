from __future__ import annotations

import os
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from production_optimizer.adapters.production.migrations import apply_migrations
from production_optimizer.adapters.production.postgres_case_repository import (
    PostgresCaseRepository,
)
from production_optimizer.contracts.state import OptimizationState


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


def _state(*, case_id: str, thread_id: str, tenant_id: str) -> OptimizationState:
    return {
        "case_id": case_id,
        "thread_id": thread_id,
        "tenant_id": tenant_id,
        "lane": "manual",
        "status": "open",
    }


def test_create_and_get_round_trip() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    case_id = f"case-{uuid4()}"
    thread_id = f"thread-{uuid4()}"
    tenant_id = f"tenant-{uuid4()}"

    with PostgresCaseRepository(dsn) as repo:
        repo.create(_state(case_id=case_id, thread_id=thread_id, tenant_id=tenant_id))
        fetched = repo.get(tenant_id=tenant_id, case_id=case_id)

    assert fetched is not None
    assert fetched.get("case_id") == case_id
    assert fetched.get("thread_id") == thread_id
    assert fetched.get("tenant_id") == tenant_id
    assert fetched.get("lane") == "manual"
    assert fetched.get("status") == "open"
    assert "current_node" not in fetched


def test_get_on_missing_case_returns_none() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    with PostgresCaseRepository(dsn) as repo:
        fetched = repo.get(tenant_id=f"tenant-{uuid4()}", case_id=f"case-{uuid4()}")

    assert fetched is None


def test_create_with_duplicate_case_id_raises() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    case_id = f"case-{uuid4()}"
    tenant_id = f"tenant-{uuid4()}"

    with PostgresCaseRepository(dsn) as repo:
        repo.create(_state(case_id=case_id, thread_id=f"thread-{uuid4()}", tenant_id=tenant_id))
        with pytest.raises(ValueError, match="already exists"):
            repo.create(
                _state(case_id=case_id, thread_id=f"thread-{uuid4()}", tenant_id=tenant_id)
            )


def test_set_status_changes_status() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    case_id = f"case-{uuid4()}"
    tenant_id = f"tenant-{uuid4()}"

    with PostgresCaseRepository(dsn) as repo:
        repo.create(_state(case_id=case_id, thread_id=f"thread-{uuid4()}", tenant_id=tenant_id))
        repo.set_status(tenant_id=tenant_id, case_id=case_id, status="closed")
        fetched = repo.get(tenant_id=tenant_id, case_id=case_id)

    assert fetched is not None
    assert fetched.get("status") == "closed"


def test_request_cancellation_sets_timestamp_and_is_idempotent() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    case_id = f"case-{uuid4()}"
    tenant_id = f"tenant-{uuid4()}"

    with PostgresCaseRepository(dsn) as repo:
        repo.create(_state(case_id=case_id, thread_id=f"thread-{uuid4()}", tenant_id=tenant_id))
        repo.request_cancellation(tenant_id=tenant_id, case_id=case_id, actor_id="actor-1")

    with (
        psycopg.Connection[DictRow].connect(dsn, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "SELECT cancellation_requested_at FROM optimizer_control.cases "
            "WHERE tenant_id = %s AND case_id = %s",
            (tenant_id, case_id),
        )
        first = cur.fetchone()

    assert first is not None
    first_timestamp = first["cancellation_requested_at"]
    assert first_timestamp is not None

    with PostgresCaseRepository(dsn) as repo:
        repo.request_cancellation(tenant_id=tenant_id, case_id=case_id, actor_id="actor-2")

    with (
        psycopg.Connection[DictRow].connect(dsn, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "SELECT cancellation_requested_at FROM optimizer_control.cases "
            "WHERE tenant_id = %s AND case_id = %s",
            (tenant_id, case_id),
        )
        second = cur.fetchone()

    assert second is not None
    assert second["cancellation_requested_at"] == first_timestamp
