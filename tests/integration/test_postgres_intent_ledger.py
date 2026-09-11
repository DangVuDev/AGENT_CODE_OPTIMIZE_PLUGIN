from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from production_optimizer.adapters.production.migrations import apply_migrations
from production_optimizer.adapters.production.postgres_case_repository import (
    PostgresCaseRepository,
)
from production_optimizer.adapters.production.postgres_intent_ledger import PostgresIntentLedger
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import IntentRecord, IntentStatus


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


def _intent(
    *, tenant_id: str, idempotency_key: str, case_id: str, node_id: str = "A1.10"
) -> IntentRecord:
    return IntentRecord(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        node_id=node_id,
        case_id=case_id,
        status=IntentStatus.PENDING,
        input_digest=f"sha256:{'a' * 64}",
        updated_at=datetime.now(UTC),
    )


def _output_ref(*, digest_character: str = "b") -> ArtifactRef:
    return ArtifactRef(
        artifact_type="SolutionPortfolio",
        schema_version="1.0",
        artifact_id="ART-1",
        content_digest=f"sha256:{digest_character * 64}",
        uri="s3://development/ART-1",
    )


def test_prepare_and_get_round_trip() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    idempotency_key = f"{case_id}:A1.10:1"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)

    with PostgresIntentLedger(dsn) as ledger:
        prepared = ledger.prepare(
            _intent(tenant_id=tenant_id, idempotency_key=idempotency_key, case_id=case_id)
        )
        fetched = ledger.get(tenant_id=tenant_id, idempotency_key=idempotency_key)

    assert prepared.status is IntentStatus.PENDING
    assert fetched is not None
    assert fetched.status is IntentStatus.PENDING
    assert fetched.output_ref is None


def test_complete_sets_output_ref_and_get_reconstructs_it() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    idempotency_key = f"{case_id}:A1.10:1"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)
    output_ref = _output_ref()

    with PostgresIntentLedger(dsn) as ledger:
        ledger.prepare(
            _intent(tenant_id=tenant_id, idempotency_key=idempotency_key, case_id=case_id)
        )
        completed = ledger.complete(
            tenant_id=tenant_id, idempotency_key=idempotency_key, output_ref=output_ref
        )
        fetched = ledger.get(tenant_id=tenant_id, idempotency_key=idempotency_key)

    assert completed.status is IntentStatus.COMPLETED
    assert completed.output_ref == output_ref
    assert fetched is not None
    assert fetched.status is IntentStatus.COMPLETED
    assert fetched.output_ref == output_ref


def test_mark_unknown_after_pending_prepare() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    idempotency_key = f"{case_id}:A1.10:1"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)

    with PostgresIntentLedger(dsn) as ledger:
        ledger.prepare(
            _intent(tenant_id=tenant_id, idempotency_key=idempotency_key, case_id=case_id)
        )
        unknown = ledger.mark_unknown(tenant_id=tenant_id, idempotency_key=idempotency_key)
        fetched = ledger.get(tenant_id=tenant_id, idempotency_key=idempotency_key)

    assert unknown.status is IntentStatus.UNKNOWN
    assert fetched is not None
    assert fetched.status is IntentStatus.UNKNOWN


def test_complete_on_never_prepared_key_raises() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    with (
        PostgresIntentLedger(dsn) as ledger,
        pytest.raises(ValueError, match="no prepared intent found"),
    ):
        ledger.complete(
            tenant_id=f"tenant-{uuid4()}",
            idempotency_key=f"missing-{uuid4()}",
            output_ref=_output_ref(),
        )


def test_prepare_twice_while_pending_does_not_create_duplicate_row() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    idempotency_key = f"{case_id}:A1.10:1"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)

    with PostgresIntentLedger(dsn) as ledger:
        first = ledger.prepare(
            _intent(tenant_id=tenant_id, idempotency_key=idempotency_key, case_id=case_id)
        )
        second = ledger.prepare(
            _intent(tenant_id=tenant_id, idempotency_key=idempotency_key, case_id=case_id)
        )

    assert first.status is IntentStatus.PENDING
    assert second.status is IntentStatus.PENDING

    with (
        psycopg.Connection[DictRow].connect(dsn, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "SELECT count(*) AS n FROM optimizer_control.node_intents "
            "WHERE tenant_id = %s AND idempotency_key = %s",
            (tenant_id, idempotency_key),
        )
        row = cur.fetchone()

    assert row is not None
    assert row["n"] == 1


def test_prepare_after_complete_does_not_downgrade_status() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    idempotency_key = f"{case_id}:A1.10:1"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)
    output_ref = _output_ref()

    with PostgresIntentLedger(dsn) as ledger:
        ledger.prepare(
            _intent(tenant_id=tenant_id, idempotency_key=idempotency_key, case_id=case_id)
        )
        ledger.complete(
            tenant_id=tenant_id, idempotency_key=idempotency_key, output_ref=output_ref
        )
        retried = ledger.prepare(
            _intent(tenant_id=tenant_id, idempotency_key=idempotency_key, case_id=case_id)
        )
        fetched = ledger.get(tenant_id=tenant_id, idempotency_key=idempotency_key)

    assert retried.status is IntentStatus.COMPLETED
    assert retried.output_ref == output_ref
    assert fetched is not None
    assert fetched.status is IntentStatus.COMPLETED
    assert fetched.output_ref == output_ref
