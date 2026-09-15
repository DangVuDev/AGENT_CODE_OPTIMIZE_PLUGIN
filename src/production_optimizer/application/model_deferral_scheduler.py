from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from production_optimizer.adapters.production import DeferredModelCallError
from production_optimizer.contracts.platform import DeferredModelCallRecord
from production_optimizer.ports.model_deferrals import ModelCallDeferralPort


class ModelDeferralSchedulerError(RuntimeError):
    """Raised when a deferred model call cannot be resumed safely."""


@dataclass(frozen=True, slots=True)
class ModelDeferralRunResult:
    deferral_id: str
    node_id: str
    status: str
    detail: str = ""


GraphFactory = Callable[[DeferredModelCallRecord], Any]


class ModelDeferralScheduler:
    """Claims due model-call deferrals and resumes the owning checkpointed graph.

    The scheduler is intentionally graph-agnostic: it only maps a deferred
    `node_id` prefix to a compiled graph factory. LangGraph owns exact resume
    semantics through the durable `thread_id` checkpoint, while this class owns
    lease/claim bookkeeping and failure classification.
    """

    def __init__(
        self,
        *,
        deferrals: ModelCallDeferralPort,
        graph_factories: Mapping[str, GraphFactory],
        tenant_id: str,
        lease_owner: str,
        lease_seconds: int = 300,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._deferrals = deferrals
        self._graph_factories = dict(graph_factories)
        self._tenant_id = tenant_id
        self._lease_owner = lease_owner
        self._lease_seconds = lease_seconds

    def run_once(self, *, limit: int = 10) -> list[ModelDeferralRunResult]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        records = self._deferrals.claim_due(
            tenant_id=self._tenant_id,
            lease_owner=self._lease_owner,
            lease_seconds=self._lease_seconds,
            limit=limit,
        )
        return [self._resume(record) for record in records]

    def _resume(self, record: DeferredModelCallRecord) -> ModelDeferralRunResult:
        if record.thread_id is None:
            self._deferrals.mark_failed(
                tenant_id=record.tenant_id,
                deferral_id=record.deferral_id,
                error_ref="model-deferral:missing-thread-id",
            )
            return ModelDeferralRunResult(
                deferral_id=record.deferral_id,
                node_id=record.node_id,
                status="failed",
                detail="missing thread_id",
            )

        graph = self._graph_for(record)
        config = {"configurable": {"thread_id": record.thread_id}}
        try:
            # With a durable LangGraph checkpointer, invoking with `None` and
            # the same thread_id resumes from the checkpointed graph state.
            graph.invoke(None, config=config)
        except DeferredModelCallError as error:
            detail = error.deferral_id or error.request_id
            return ModelDeferralRunResult(
                deferral_id=record.deferral_id,
                node_id=record.node_id,
                status="deferred",
                detail=detail,
            )
        except Exception as error:
            self._deferrals.mark_failed(
                tenant_id=record.tenant_id,
                deferral_id=record.deferral_id,
                error_ref=f"model-deferral:{type(error).__name__}",
            )
            return ModelDeferralRunResult(
                deferral_id=record.deferral_id,
                node_id=record.node_id,
                status="failed",
                detail=f"{type(error).__name__}: {error}",
            )

        self._deferrals.mark_succeeded(
            tenant_id=record.tenant_id,
            deferral_id=record.deferral_id,
        )
        return ModelDeferralRunResult(
            deferral_id=record.deferral_id,
            node_id=record.node_id,
            status="succeeded",
        )

    def _graph_for(self, record: DeferredModelCallRecord) -> Any:
        stage = record.node_id.split(".", maxsplit=1)[0]
        factory = self._graph_factories.get(stage)
        if factory is None:
            raise ModelDeferralSchedulerError(f"no graph factory registered for {stage}")
        return factory(record)
