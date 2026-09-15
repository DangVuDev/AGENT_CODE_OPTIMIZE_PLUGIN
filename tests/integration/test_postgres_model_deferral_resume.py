# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph

from production_optimizer.adapters.production import (
    DeferredModelCallError,
    ModelProviderCandidate,
    PostgresCaseRepository,
    PostgresCheckpointProvider,
    PostgresModelCallDeferralStore,
    ResilientModelGateway,
    apply_migrations,
)
from production_optimizer.application.model_deferral_scheduler import ModelDeferralScheduler
from production_optimizer.contracts.platform import (
    DeferredModelCallStatus,
    ModelCompletionRequest,
    ModelCompletionResult,
    ModelMessage,
    ModelRole,
)
from production_optimizer.contracts.state import OptimizationState


def _dsn() -> str:
    return os.environ.get(
        "OPTIMIZER_DATABASE_DSN",
        "postgresql://optimizer:change-me@127.0.0.1:5432/optimizer",
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


class _Retryable503(RuntimeError):
    status_code = 503


class _FailingModelProvider:
    def __init__(self) -> None:
        self.requests: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.requests.append(request)
        raise _Retryable503("provider unavailable")

    def healthcheck(self) -> bool:
        return True


class _SuccessfulModelProvider:
    def __init__(self) -> None:
        self.requests: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.requests.append(request)
        return ModelCompletionResult(
            request_id=request.idempotency_key,
            model_id=request.model_id,
            model_version="test-success-v1",
            raw_text='{"ok": true}',
            parsed_json={"ok": True},
            valid_json=True,
            input_tokens=7,
            output_tokens=3,
            stop_reason="stop",
        )

    def healthcheck(self) -> bool:
        return True


def _initial_state(*, tenant_id: str, case_id: str, thread_id: str) -> OptimizationState:
    return {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "thread_id": thread_id,
        "lane": "manual",
        "entrypoint": "manual",
        "status": "running",
        "completed_nodes": [],
        "node_routes": {},
        "artifact_refs": [],
        "error_refs": [],
        "event_refs": [],
    }


def _model_request(case_id: str) -> ModelCompletionRequest:
    return ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id="gateway-placeholder",
        prompt_version="test-a3-resume-v1",
        messages=[ModelMessage(role="user", content="Generate one optimization finding.")],
        response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        max_output_tokens=64,
        idempotency_key=f"{case_id}:A3.40:test-model-call",
    )


def _gateway(
    *,
    provider: Any,
    tenant_id: str,
    thread_id: str,
    deferrals: PostgresModelCallDeferralStore,
) -> ResilientModelGateway:
    return ResilientModelGateway(
        [
            ModelProviderCandidate(
                provider_name="primary",
                provider=provider,
                model_id="test-model",
            )
        ],
        tenant_id=tenant_id,
        thread_id=thread_id,
        deferrals=deferrals,
        cooldown_seconds=0,
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def _build_checkpointed_graph(
    *,
    model: ResilientModelGateway,
    checkpointer: Any,
) -> Any:
    def checkpoint_anchor(state: OptimizationState) -> dict[str, Any]:
        return {
            "current_node": "A3.10",
            "completed_nodes": ["A3.10"],
        }

    def model_node(state: OptimizationState) -> dict[str, Any]:
        case_id = state.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("checkpointed state is missing case_id")
        result = model.complete(_model_request(case_id))
        return {
            "current_node": "A3.40",
            "completed_nodes": ["A3.40"],
            "status": f"model:{result.stop_reason}",
        }

    builder = StateGraph(OptimizationState)
    builder.add_node("A3.10", checkpoint_anchor)
    builder.add_node("A3.40", model_node)
    builder.add_edge(START, "A3.10")
    builder.add_edge("A3.10", "A3.40")
    builder.add_edge("A3.40", END)
    return builder.compile(checkpointer=checkpointer)


def test_postgres_deferral_scheduler_resumes_checkpointed_langgraph_node() -> None:
    dsn = _dsn()
    _require_database(dsn)
    apply_migrations(dsn)

    tenant_id = f"tenant-{uuid4()}"
    case_id = f"case-{uuid4()}"
    thread_id = f"thread-{uuid4()}"
    initial_state = _initial_state(tenant_id=tenant_id, case_id=case_id, thread_id=thread_id)

    with PostgresCaseRepository(dsn) as cases:
        cases.create(initial_state)

    failing_provider = _FailingModelProvider()
    successful_provider = _SuccessfulModelProvider()

    with (
        PostgresModelCallDeferralStore(dsn) as deferrals,
        PostgresCheckpointProvider(dsn) as checkpoints,
    ):
        failing_graph = _build_checkpointed_graph(
            model=_gateway(
                provider=failing_provider,
                tenant_id=tenant_id,
                thread_id=thread_id,
                deferrals=deferrals,
            ),
            checkpointer=checkpoints.checkpointer(),
        )

        with pytest.raises(DeferredModelCallError) as deferred:
            failing_graph.invoke(initial_state, config={"configurable": {"thread_id": thread_id}})

        assert deferred.value.deferral_id is not None
        scheduled = deferrals.get(tenant_id=tenant_id, deferral_id=deferred.value.deferral_id)
        assert scheduled is not None
        assert scheduled.status is DeferredModelCallStatus.SCHEDULED
        assert scheduled.case_id == case_id
        assert scheduled.thread_id == thread_id
        assert scheduled.node_id == "A3.40"
        assert scheduled.attempts == 0

        def graph_factory(_record: Any) -> Any:
            return _build_checkpointed_graph(
                model=_gateway(
                    provider=successful_provider,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    deferrals=deferrals,
                ),
                checkpointer=checkpoints.checkpointer(),
            )

        scheduler = ModelDeferralScheduler(
            deferrals=deferrals,
            graph_factories={"A3": graph_factory},
            tenant_id=tenant_id,
            lease_owner="integration-test-worker",
            lease_seconds=60,
        )

        results = scheduler.run_once(limit=1)

        assert [result.status for result in results] == ["succeeded"]
        assert len(failing_provider.requests) == 1
        assert len(successful_provider.requests) == 1

        finished = deferrals.get(tenant_id=tenant_id, deferral_id=deferred.value.deferral_id)
        assert finished is not None
        assert finished.status is DeferredModelCallStatus.SUCCEEDED
        assert finished.attempts == 1
        assert finished.lease_owner is None
        assert finished.lease_expires_at is None
