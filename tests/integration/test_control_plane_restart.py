"""FRAME-0 proof: no duplicate side effects across a control-plane restart.

Covers the two crash windows `NodeRuntime._execute_idempotent` is designed
around (crash before the effect's outcome is known -> intent stays `pending`;
crash after the effect but before it is safely recorded -> intent is marked
`unknown` rather than silently left `pending` or wrongly `completed`), plus
the end-to-end proof that a real `NodeRuntime`, torn down and rebuilt against
the same PostgreSQL-backed `IntentLedger`, replays a completed intent's
output instead of re-running the handler.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest

from production_optimizer.adapters.production.migrations import apply_migrations
from production_optimizer.adapters.production.postgres_case_repository import (
    PostgresCaseRepository,
)
from production_optimizer.adapters.production.postgres_intent_ledger import PostgresIntentLedger
from production_optimizer.application import (
    BusinessNodeHandler,
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    NodeSpec,
    RegisteredNode,
    SideEffectClass,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import IntentRecord, IntentStatus, TelemetryEvent
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


class _InMemoryArtifactStore:
    """Stand-in `ArtifactStore`: kept alive across the "restarted" NodeRuntime
    instances in this test, the same way a real object-storage service would
    outlive any single in-process `NodeRuntime`."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put_json(
        self, *, tenant_id: str, content: bytes, content_digest: str, idempotency_key: str
    ) -> ArtifactRef:
        del tenant_id
        artifact_id = f"intent-output:{idempotency_key}"
        self._blobs[artifact_id] = content
        return ArtifactRef(
            artifact_type="NodeExecutionOutput",
            schema_version="1.0",
            artifact_id=artifact_id,
            content_digest=content_digest,
            uri=f"memory://{artifact_id}",
        )

    def put_blob(
        self, *, tenant_id: str, content: bytes, content_digest: str, media_type: str
    ) -> ArtifactRef:
        raise NotImplementedError("not exercised by this test")

    def read(self, *, tenant_id: str, ref: ArtifactRef) -> bytes:
        del tenant_id
        return self._blobs[ref.artifact_id]

    def verify(self, *, tenant_id: str, ref: ArtifactRef) -> bool:
        del tenant_id
        return ref.artifact_id in self._blobs


class _RecordingTelemetryPort:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)

    def flush(self, *, timeout_seconds: float) -> None:
        del timeout_seconds


def _write_spec(node_id: str) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="test",
        input_contract="Input@1.0",
        output_contract="Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=5,
        max_attempts=1,
        allowed_routes={"continue"},
        runbook="docs/runbooks/test.md",
        slo="test",
    )


def _counting_handler(calls: list[int]) -> BusinessNodeHandler:
    def execute(_state: OptimizationState, _ports: NodePorts | None, /) -> NodeExecution:
        calls.append(1)
        return NodeExecution(route=NodeRoute.CONTINUE, updates={})

    return execute


def test_intent_ledger_survives_restart_after_prepare_without_complete() -> None:
    """Crash before the effect's outcome is known: a fresh ledger still sees `pending`."""

    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    idempotency_key = f"{case_id}:A1.10:1"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)

    with PostgresIntentLedger(dsn) as ledger:
        ledger.prepare(
            IntentRecord(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                node_id="A1.10",
                case_id=case_id,
                status=IntentStatus.PENDING,
                input_digest=f"sha256:{'a' * 64}",
                updated_at=datetime.now(UTC),
            )
        )
        # no complete() call: simulates a crash mid-effect

    # A fresh instance stands in for a new process reconnecting after restart.
    with PostgresIntentLedger(dsn) as restarted:
        fetched = restarted.get(tenant_id=tenant_id, idempotency_key=idempotency_key)

    assert fetched is not None
    assert fetched.status is IntentStatus.PENDING


def test_intent_ledger_survives_restart_after_mark_unknown() -> None:
    """Crash after the effect but before it is safely recorded: get() shows `unknown`."""

    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    idempotency_key = f"{case_id}:A1.10:1"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)

    with PostgresIntentLedger(dsn) as ledger:
        ledger.prepare(
            IntentRecord(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                node_id="A1.10",
                case_id=case_id,
                status=IntentStatus.PENDING,
                input_digest=f"sha256:{'a' * 64}",
                updated_at=datetime.now(UTC),
            )
        )
        # The external side effect may or may not have landed; the caller
        # cannot tell, so it marks the intent unknown rather than completed.
        ledger.mark_unknown(tenant_id=tenant_id, idempotency_key=idempotency_key)

    with PostgresIntentLedger(dsn) as restarted:
        fetched = restarted.get(tenant_id=tenant_id, idempotency_key=idempotency_key)

    assert fetched is not None
    assert fetched.status is IntentStatus.UNKNOWN


def test_node_runtime_does_not_duplicate_side_effects_across_restart() -> None:
    """The single most important test in this batch.

    Executes the same `IDEMPOTENT_WRITE` node twice with identical state, once
    per a freshly constructed `NodeRuntime` -- simulating an in-process
    restart with zero shared memory between the two calls -- and asserts the
    handler only actually ran once. The second `NodeRuntime` has no idea the
    first one existed; it only knows because `PostgresIntentLedger.get()`
    reports the intent as already `completed`.
    """

    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    _create_case(dsn, tenant_id=tenant_id, case_id=case_id)

    node_id = "A1.10"
    state: OptimizationState = {
        "case_id": case_id,
        "thread_id": f"thread-{uuid4()}",
        "tenant_id": tenant_id,
        "lane": "manual",
        "status": "open",
    }
    calls: list[int] = []
    artifacts = _InMemoryArtifactStore()

    with PostgresIntentLedger(dsn) as ledger_one:
        ports_one = NodePorts(
            artifacts=artifacts, intents=ledger_one, telemetry=_RecordingTelemetryPort()
        )
        runtime_one = NodeRuntime(
            {node_id: RegisteredNode(spec=_write_spec(node_id), handler=_counting_handler(calls))},
            ports=ports_one,
        )
        runtime_one.execute(node_id, state)

    assert calls == [1]

    with PostgresIntentLedger(dsn) as ledger_two:
        ports_two = NodePorts(
            artifacts=artifacts, intents=ledger_two, telemetry=_RecordingTelemetryPort()
        )
        runtime_two = NodeRuntime(
            {node_id: RegisteredNode(spec=_write_spec(node_id), handler=_counting_handler(calls))},
            ports=ports_two,
        )
        runtime_two.execute(node_id, state)

    assert calls == [1]
