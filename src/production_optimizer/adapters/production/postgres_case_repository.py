from __future__ import annotations

from typing import Any

from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from production_optimizer.contracts.state import OptimizationState

_REQUIRED_FIELDS = ("case_id", "thread_id", "tenant_id", "lane", "status")


def require_field(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"OptimizationState is missing required field {key!r} for case creation")
    return value


class PostgresCaseRepository:
    """PostgreSQL-backed `CaseRepository` over `optimizer_control.cases`.

    `cases` is a case-index projection, not the full checkpointed state: `get`
    reconstructs only the columns this table owns (`case_id`, `thread_id`,
    `tenant_id`, `lane`, `status`, `current_node`), never the rich fields
    (artifact refs, event refs, interrupts, ...) that live in the LangGraph
    checkpoint. Callers needing the full `OptimizationState` must go through
    the checkpointer, not this repository.

    `request_cancellation`'s `actor_id` is intentionally not persisted here:
    this table is a status index, not an audit trail, and it has no
    `actor_id` column. Actor attribution for a cancellation request belongs to
    the (future) audit-event system, not the case-index row; this adapter
    only records the timestamp.
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
            name="optimizer-case-repository",
        )
        try:
            pool.open(wait=True, timeout=connect_timeout_seconds)
        except Exception:
            pool.close()
            raise
        self._pool: ConnectionPool[Any] | None = pool

    def _require_pool(self) -> ConnectionPool[Any]:
        if self._pool is None:
            raise RuntimeError("PostgresCaseRepository has been closed")
        return self._pool

    def create(self, state: OptimizationState) -> None:
        values = {key: require_field(state, key) for key in _REQUIRED_FIELDS}
        current_node = state.get("current_node")

        pool = self._require_pool()
        try:
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO optimizer_control.cases
                        (tenant_id, case_id, thread_id, lane, status, current_node)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        values["tenant_id"],
                        values["case_id"],
                        values["thread_id"],
                        values["lane"],
                        values["status"],
                        current_node,
                    ),
                )
        except errors.UniqueViolation as exc:
            raise ValueError(
                f"case already exists for tenant_id={values['tenant_id']!r} "
                f"case_id={values['case_id']!r}"
            ) from exc

    def get(self, *, tenant_id: str, case_id: str) -> OptimizationState | None:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT case_id, thread_id, tenant_id, lane, status, current_node
                FROM optimizer_control.cases
                WHERE tenant_id = %s AND case_id = %s
                """,
                (tenant_id, case_id),
            )
            row = cur.fetchone()

        if row is None:
            return None

        state: OptimizationState = {
            "case_id": row["case_id"],
            "thread_id": row["thread_id"],
            "tenant_id": row["tenant_id"],
            "lane": row["lane"],
            "status": row["status"],
        }
        if row["current_node"] is not None:
            state["current_node"] = row["current_node"]
        return state

    def set_status(self, *, tenant_id: str, case_id: str, status: str) -> None:
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE optimizer_control.cases
                SET status = %s, updated_at = now()
                WHERE tenant_id = %s AND case_id = %s
                """,
                (status, tenant_id, case_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"no case found for tenant_id={tenant_id!r} case_id={case_id!r}")

    def request_cancellation(self, *, tenant_id: str, case_id: str, actor_id: str) -> None:
        del actor_id  # see class docstring: not persisted on this table
        pool = self._require_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE optimizer_control.cases
                SET cancellation_requested_at = now()
                WHERE tenant_id = %s AND case_id = %s AND cancellation_requested_at IS NULL
                """,
                (tenant_id, case_id),
            )

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

    def __enter__(self) -> PostgresCaseRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
