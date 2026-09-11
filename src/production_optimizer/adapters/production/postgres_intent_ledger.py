from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import IntentRecord, IntentStatus

_SELECT_COLUMNS = """
    tenant_id, idempotency_key, case_id, node_id, input_digest, status,
    output_artifact_type, output_schema_version, output_artifact_id,
    output_digest, output_uri, updated_at
"""


def artifact_ref_from_row(row: Mapping[str, Any]) -> ArtifactRef | None:
    """Reconstruct a full `ArtifactRef` from `node_intents`' flattened output_* columns.

    `ArtifactRef` needs `artifact_type`, `schema_version`, `artifact_id`,
    `content_digest`, and `uri`; the table stores those as
    `output_artifact_type`, `output_schema_version`, `output_artifact_id`,
    `output_digest`, and `output_uri` respectively. A pending or unknown
    intent has all five columns NULL, in which case there is no output yet
    and this returns `None`; any other combination of NULLs would mean a
    corrupt row, so this treats "any field missing" the same as "no output".
    """

    artifact_type = row.get("output_artifact_type")
    schema_version = row.get("output_schema_version")
    artifact_id = row.get("output_artifact_id")
    content_digest = row.get("output_digest")
    uri = row.get("output_uri")
    if (
        artifact_type is None
        or schema_version is None
        or artifact_id is None
        or content_digest is None
        or uri is None
    ):
        return None
    return ArtifactRef(
        artifact_type=artifact_type,
        schema_version=schema_version,
        artifact_id=artifact_id,
        content_digest=content_digest,
        uri=uri,
    )


def intent_record_from_row(row: Mapping[str, Any]) -> IntentRecord:
    return IntentRecord(
        tenant_id=row["tenant_id"],
        idempotency_key=row["idempotency_key"],
        node_id=row["node_id"],
        case_id=row["case_id"],
        status=IntentStatus(row["status"]),
        input_digest=row["input_digest"],
        output_ref=artifact_ref_from_row(row),
        updated_at=row["updated_at"],
    )


class PostgresIntentLedger:
    """PostgreSQL-backed `IntentLedger` over `optimizer_control.node_intents`.

    This is the crash-recovery boundary for `NodeRuntime._execute_idempotent`:
    `prepare` must be safe to call more than once for the same
    `(tenant_id, idempotency_key)` under concurrent retries, and must never
    let a retry downgrade an already-`completed` intent back to `pending`.
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
            name="optimizer-intent-ledger",
        )
        try:
            pool.open(wait=True, timeout=connect_timeout_seconds)
        except Exception:
            pool.close()
            raise
        self._pool: ConnectionPool[Any] | None = pool

    def _require_pool(self) -> ConnectionPool[Any]:
        if self._pool is None:
            raise RuntimeError("PostgresIntentLedger has been closed")
        return self._pool

    def prepare(self, intent: IntentRecord) -> IntentRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO optimizer_control.node_intents
                    (tenant_id, idempotency_key, case_id, node_id, input_digest,
                     status, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, idempotency_key) DO UPDATE SET
                    case_id = EXCLUDED.case_id,
                    node_id = EXCLUDED.node_id,
                    input_digest = EXCLUDED.input_digest,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at
                WHERE optimizer_control.node_intents.status <> 'completed'
                """,
                (
                    intent.tenant_id,
                    intent.idempotency_key,
                    intent.case_id,
                    intent.node_id,
                    intent.input_digest,
                    intent.status.value,
                    intent.updated_at,
                ),
            )
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM optimizer_control.node_intents "
                "WHERE tenant_id = %s AND idempotency_key = %s",
                (intent.tenant_id, intent.idempotency_key),
            )
            row = cur.fetchone()

        if row is None:  # pragma: no cover - defensive; the insert always creates the row
            raise RuntimeError("intent prepare did not persist a row")
        return intent_record_from_row(row)

    def get(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord | None:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM optimizer_control.node_intents "
                "WHERE tenant_id = %s AND idempotency_key = %s",
                (tenant_id, idempotency_key),
            )
            row = cur.fetchone()

        return None if row is None else intent_record_from_row(row)

    def complete(
        self, *, tenant_id: str, idempotency_key: str, output_ref: ArtifactRef
    ) -> IntentRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE optimizer_control.node_intents
                SET status = 'completed',
                    output_artifact_type = %s,
                    output_schema_version = %s,
                    output_artifact_id = %s,
                    output_digest = %s,
                    output_uri = %s,
                    updated_at = now()
                WHERE tenant_id = %s AND idempotency_key = %s
                RETURNING {_SELECT_COLUMNS}
                """,
                (
                    output_ref.artifact_type,
                    output_ref.schema_version,
                    output_ref.artifact_id,
                    output_ref.content_digest,
                    output_ref.uri,
                    tenant_id,
                    idempotency_key,
                ),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(
                "cannot complete intent: no prepared intent found for "
                f"tenant_id={tenant_id!r} idempotency_key={idempotency_key!r}"
            )
        return intent_record_from_row(row)

    def mark_unknown(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE optimizer_control.node_intents
                SET status = 'unknown', updated_at = now()
                WHERE tenant_id = %s AND idempotency_key = %s AND status = 'pending'
                RETURNING {_SELECT_COLUMNS}
                """,
                (tenant_id, idempotency_key),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(
                "cannot mark intent unknown: no pending intent found for "
                f"tenant_id={tenant_id!r} idempotency_key={idempotency_key!r}"
            )
        return intent_record_from_row(row)

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

    def __enter__(self) -> PostgresIntentLedger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
