from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from production_optimizer.contracts.platform import OutboxRecord

_SELECT_COLUMNS = """
    event_id, tenant_id, case_id, topic, payload_ref, payload_digest,
    available_at, delivered_at, attempts, last_error_ref
"""


def outbox_record_from_row(row: Mapping[str, Any]) -> OutboxRecord:
    """Reconstruct an `OutboxRecord` from an `optimizer_control.outbox` row.

    `event_id` is a Postgres `uuid` column, so psycopg returns it as a
    `uuid.UUID`; `OutboxRecord.event_id` is a plain `str`, so it is converted
    here.
    """

    return OutboxRecord(
        event_id=str(row["event_id"]),
        tenant_id=row["tenant_id"],
        case_id=row["case_id"],
        topic=row["topic"],
        payload_ref=row["payload_ref"],
        payload_digest=row["payload_digest"],
        available_at=row["available_at"],
        delivered_at=row["delivered_at"],
        attempts=row["attempts"],
        last_error_ref=row["last_error_ref"],
    )


class PostgresOutboxAdapter:
    """PostgreSQL-backed `OutboxPort` over `optimizer_control.outbox`.

    Implements the standard transactional-outbox polling pattern: `claim`
    uses ``FOR UPDATE SKIP LOCKED`` so concurrent claimers never select the
    same row, and `publish` is idempotent on `event_id` so a caller crashing
    between writing the row and acknowledging it can safely retry the same
    publish call.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        connect_timeout_seconds: float = 30.0,
    ) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be empty")
        if min_pool_size < 1 or max_pool_size < min_pool_size:
            raise ValueError("invalid PostgreSQL connection-pool bounds")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect timeout must be positive")

        pool: ConnectionPool[Any] = ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=min_pool_size,
            max_size=max_pool_size,
            open=False,
            name="optimizer-outbox",
        )
        try:
            pool.open(wait=True, timeout=connect_timeout_seconds)
        except Exception:
            pool.close()
            raise
        self._pool: ConnectionPool[Any] | None = pool

    def _require_pool(self) -> ConnectionPool[Any]:
        if self._pool is None:
            raise RuntimeError("PostgresOutboxAdapter has been closed")
        return self._pool

    def publish(self, record: OutboxRecord) -> OutboxRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO optimizer_control.outbox
                    (event_id, tenant_id, case_id, topic, payload_ref, payload_digest,
                     available_at, delivered_at, attempts, last_error_ref)
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING {_SELECT_COLUMNS}
                """,
                (
                    record.event_id,
                    record.tenant_id,
                    record.case_id,
                    record.topic,
                    record.payload_ref,
                    record.payload_digest,
                    record.available_at,
                    record.delivered_at,
                    record.attempts,
                    record.last_error_ref,
                ),
            )
            row = cur.fetchone()
            if row is None:
                # ON CONFLICT DO NOTHING skipped the insert: idempotent replay of an
                # already-published event_id. Return the row as it exists today.
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM optimizer_control.outbox "
                    "WHERE event_id = %s::uuid",
                    (record.event_id,),
                )
                row = cur.fetchone()

        if row is None:  # pragma: no cover - defensive
            raise RuntimeError("outbox publish could not persist or locate the record")
        return outbox_record_from_row(row)

    def claim(self, *, tenant_id: str, limit: int) -> list[OutboxRecord]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS} FROM optimizer_control.outbox
                WHERE tenant_id = %s AND delivered_at IS NULL AND available_at <= now()
                ORDER BY available_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (tenant_id, limit),
            )
            rows = cur.fetchall()

        return [outbox_record_from_row(row) for row in rows]

    def mark_delivered(self, *, tenant_id: str, event_id: str) -> OutboxRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE optimizer_control.outbox
                SET delivered_at = now()
                WHERE tenant_id = %s AND event_id = %s::uuid
                RETURNING {_SELECT_COLUMNS}
                """,
                (tenant_id, event_id),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(
                f"no outbox record found for tenant_id={tenant_id!r} event_id={event_id!r}"
            )
        return outbox_record_from_row(row)

    def mark_failed(self, *, tenant_id: str, event_id: str, error_ref: str) -> OutboxRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE optimizer_control.outbox
                SET attempts = attempts + 1, last_error_ref = %s
                WHERE tenant_id = %s AND event_id = %s::uuid
                RETURNING {_SELECT_COLUMNS}
                """,
                (error_ref, tenant_id, event_id),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(
                f"no outbox record found for tenant_id={tenant_id!r} event_id={event_id!r}"
            )
        return outbox_record_from_row(row)

    def healthcheck(self) -> bool:
        pool = self._pool
        if pool is None:
            return False
        try:
            pool.check()
        except Exception:
            return False
        return True

    def close(self) -> None:
        pool = self._pool
        self._pool = None
        if pool is not None:
            pool.close()

    def __enter__(self) -> PostgresOutboxAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
