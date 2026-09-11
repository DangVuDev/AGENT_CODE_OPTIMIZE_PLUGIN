# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from typing import Any, cast

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from production_optimizer.contracts import ArtifactRef, EventRef, InterruptEnvelope


def strict_checkpoint_serializer() -> JsonPlusSerializer:
    """Serializer allowlisting only checkpoint-safe product contract types."""

    allowed_types = (ArtifactRef, EventRef, InterruptEnvelope)
    allowed_modules = tuple((item.__module__, item.__name__) for item in allowed_types)
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=allowed_modules,
        allowed_msgpack_modules=allowed_types,
    )


class PostgresCheckpointProvider:
    """Lifecycle owner for the production LangGraph PostgreSQL checkpointer.

    Connections use autocommit as required by the upstream checkpointer. The
    provider must be set up during application startup and closed during
    shutdown; it never embeds or logs the DSN.
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

        self._dsn = dsn
        self._min_pool_size = min_pool_size
        self._max_pool_size = max_pool_size
        self._connect_timeout_seconds = connect_timeout_seconds
        self._pool: ConnectionPool[Any] | None = None
        self._checkpointer: PostgresSaver | None = None

    def setup(self) -> None:
        if self._checkpointer is not None:
            return
        pool = ConnectionPool(
            conninfo=self._dsn,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            min_size=self._min_pool_size,
            max_size=self._max_pool_size,
            open=False,
            name="optimizer-checkpoints",
        )
        try:
            pool.open(wait=True, timeout=self._connect_timeout_seconds)
            checkpointer = PostgresSaver(
                cast(Any, pool), serde=strict_checkpoint_serializer()
            )
            checkpointer.setup()
        except Exception:
            pool.close()
            raise
        self._pool = pool
        self._checkpointer = checkpointer

    def checkpointer(self) -> PostgresSaver:
        if self._checkpointer is None:
            raise RuntimeError("PostgreSQL checkpoint provider has not been set up")
        return self._checkpointer

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
        self._checkpointer = None
        if pool is not None:
            pool.close()

    def __enter__(self) -> PostgresCheckpointProvider:
        self.setup()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
