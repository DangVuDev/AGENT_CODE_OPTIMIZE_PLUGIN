from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from production_optimizer.contracts.platform import (
    DeferredModelCallRecord,
    DeferredModelCallStatus,
    ModelCallFailureRecord,
    ModelRole,
)

_COLUMNS = (
    "tenant_id",
    "deferral_id",
    "case_id",
    "thread_id",
    "node_id",
    "idempotency_key",
    "request_digest",
    "status",
    "role",
    "prompt_version",
    "primary_model_id",
    "failures",
    "retry_after_seconds",
    "available_at",
    "lease_owner",
    "lease_expires_at",
    "attempts",
    "last_error_ref",
    "created_at",
    "updated_at",
)

_SELECT_COLUMNS = ", ".join(_COLUMNS)
_RETURNING_D_COLUMNS = ", ".join(f"d.{column} AS {column}" for column in _COLUMNS)


def model_call_deferral_from_row(row: Mapping[str, Any]) -> DeferredModelCallRecord:
    failures = [
        ModelCallFailureRecord.model_validate(item) for item in list(row["failures"] or [])
    ]
    return DeferredModelCallRecord(
        tenant_id=row["tenant_id"],
        deferral_id=row["deferral_id"],
        case_id=row["case_id"],
        thread_id=row["thread_id"],
        node_id=row["node_id"],
        idempotency_key=row["idempotency_key"],
        request_digest=row["request_digest"],
        status=DeferredModelCallStatus(row["status"]),
        role=ModelRole(row["role"]),
        prompt_version=row["prompt_version"],
        primary_model_id=row["primary_model_id"],
        failures=failures,
        retry_after_seconds=row["retry_after_seconds"],
        available_at=row["available_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        attempts=row["attempts"],
        last_error_ref=row["last_error_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresModelCallDeferralStore:
    """PostgreSQL-backed retry-later queue for exhausted model calls."""

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
            name="optimizer-model-deferrals",
        )
        try:
            pool.open(wait=True, timeout=connect_timeout_seconds)
        except Exception:
            pool.close()
            raise
        self._pool: ConnectionPool[Any] | None = pool

    def _require_pool(self) -> ConnectionPool[Any]:
        if self._pool is None:
            raise RuntimeError("PostgresModelCallDeferralStore has been closed")
        return self._pool

    def defer(self, record: DeferredModelCallRecord) -> DeferredModelCallRecord:
        pool = self._require_pool()
        failures = [failure.model_dump(mode="json") for failure in record.failures]
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO optimizer_control.model_call_deferrals
                    (tenant_id, deferral_id, case_id, thread_id, node_id, idempotency_key,
                     request_digest, status, role, prompt_version, primary_model_id, failures,
                     retry_after_seconds, available_at, lease_owner, lease_expires_at, attempts,
                     last_error_ref, created_at, updated_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s)
                ON CONFLICT (tenant_id, deferral_id) DO UPDATE SET
                    status = CASE
                        WHEN optimizer_control.model_call_deferrals.status = 'succeeded'
                        THEN optimizer_control.model_call_deferrals.status
                        ELSE EXCLUDED.status
                    END,
                    failures = EXCLUDED.failures,
                    retry_after_seconds = EXCLUDED.retry_after_seconds,
                    available_at = EXCLUDED.available_at,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_ref = EXCLUDED.last_error_ref,
                    updated_at = EXCLUDED.updated_at
                RETURNING {_SELECT_COLUMNS}
                """,
                (
                    record.tenant_id,
                    record.deferral_id,
                    record.case_id,
                    record.thread_id,
                    record.node_id,
                    record.idempotency_key,
                    record.request_digest,
                    record.status.value,
                    record.role.value,
                    record.prompt_version,
                    record.primary_model_id,
                    Jsonb(failures),
                    record.retry_after_seconds,
                    record.available_at,
                    record.lease_owner,
                    record.lease_expires_at,
                    record.attempts,
                    record.last_error_ref,
                    record.created_at,
                    record.updated_at,
                ),
            )
            row = cur.fetchone()

        if row is None:  # pragma: no cover - defensive
            raise RuntimeError("model call deferral was not persisted")
        return model_call_deferral_from_row(row)

    def get(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord | None:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM optimizer_control.model_call_deferrals "
                "WHERE tenant_id = %s AND deferral_id = %s",
                (tenant_id, deferral_id),
            )
            row = cur.fetchone()
        return None if row is None else model_call_deferral_from_row(row)

    def claim_due(
        self, *, tenant_id: str, lease_owner: str, lease_seconds: int, limit: int
    ) -> list[DeferredModelCallRecord]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                WITH due AS (
                    SELECT tenant_id, deferral_id
                    FROM optimizer_control.model_call_deferrals
                    WHERE tenant_id = %s
                      AND (
                        (status = 'scheduled' AND available_at <= now())
                        OR (status = 'running' AND lease_expires_at <= now())
                      )
                    ORDER BY available_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE optimizer_control.model_call_deferrals d
                SET status = 'running',
                    lease_owner = %s,
                    lease_expires_at = now() + make_interval(secs => %s),
                    attempts = d.attempts + 1,
                    updated_at = now()
                FROM due
                WHERE d.tenant_id = due.tenant_id AND d.deferral_id = due.deferral_id
                RETURNING {_RETURNING_D_COLUMNS}
                """,
                (tenant_id, limit, lease_owner, lease_seconds),
            )
            rows = cur.fetchall()
        return [model_call_deferral_from_row(row) for row in rows]

    def mark_succeeded(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE optimizer_control.model_call_deferrals
                SET status = 'succeeded',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE tenant_id = %s AND deferral_id = %s
                RETURNING {_SELECT_COLUMNS}
                """,
                (tenant_id, deferral_id),
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"no model call deferral found for {tenant_id=} {deferral_id=}")
        return model_call_deferral_from_row(row)

    def mark_failed(
        self, *, tenant_id: str, deferral_id: str, error_ref: str
    ) -> DeferredModelCallRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE optimizer_control.model_call_deferrals
                SET status = 'failed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_ref = %s,
                    updated_at = now()
                WHERE tenant_id = %s AND deferral_id = %s
                RETURNING {_SELECT_COLUMNS}
                """,
                (error_ref, tenant_id, deferral_id),
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"no model call deferral found for {tenant_id=} {deferral_id=}")
        return model_call_deferral_from_row(row)

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

    def __enter__(self) -> PostgresModelCallDeferralStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
