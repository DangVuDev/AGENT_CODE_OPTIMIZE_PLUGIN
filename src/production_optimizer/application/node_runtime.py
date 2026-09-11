from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, cast, get_type_hints

from pydantic import BaseModel, Field, TypeAdapter

from production_optimizer.contracts.base import ContractModel
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.platform import IntentRecord, IntentStatus, TelemetryEvent
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.ports.artifacts import ArtifactStore
from production_optimizer.ports.intents import IntentLedger
from production_optimizer.ports.telemetry import TelemetryPort

from .node_contract import NodeSpec, SideEffectClass


class NodeRoute(StrEnum):
    CONTINUE = "continue"
    CLARIFICATION = "clarification"
    APPROVAL = "approval"
    REJECTED = "rejected"
    MISSING = "missing"
    INCOMPARABLE = "incomparable"
    REVISION = "revision"
    QUARANTINE = "quarantine"
    MERGED = "merged"
    SUPPRESSED = "suppressed"
    REFRESH = "refresh"
    CLOSED = "closed"


class NodeExecution(ContractModel):
    """Validated output returned by an application handler.

    Handlers return compact checkpoint updates only. Raw output must first be
    persisted by an artifact adapter and represented here by a reference.
    """

    route: NodeRoute = NodeRoute.CONTINUE
    updates: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NodePorts:
    """Capability bundle injected into a business-node handler.

    Only handlers whose `NodeSpec.side_effect_class` is not `PURE` are
    guaranteed a non-`None` bundle when the owning `NodeRuntime` is composed
    with real adapters; `telemetry` remains optional even then.
    """

    artifacts: ArtifactStore
    intents: IntentLedger
    telemetry: TelemetryPort | None = None


class BusinessNodeHandler(Protocol):
    def __call__(self, state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution: ...


class NodeNotEnabledError(RuntimeError):
    """Raised when topology exists but a production handler is not enabled."""


class InvalidNodeExecutionError(RuntimeError):
    """Raised when a handler attempts an undeclared route or state mutation."""


@dataclass(frozen=True, slots=True)
class RegisteredNode:
    spec: NodeSpec
    handler: BusinessNodeHandler


_IDENTITY_FIELDS = frozenset({"case_id", "thread_id", "tenant_id", "entrypoint", "lane"})
_RUNTIME_FIELDS = frozenset(OptimizationState.__annotations__)
_STATE_FIELD_TYPES = get_type_hints(OptimizationState, include_extras=True)


def _json_safe(value: Any) -> Any:
    """Recursively convert contract models into plain JSON-serializable data."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        items = cast("dict[Any, Any]", value)
        safe_dict: dict[Any, Any] = {key: _json_safe(item) for key, item in items.items()}
        return safe_dict
    if isinstance(value, list):
        entries = cast("list[Any]", value)
        safe_list: list[Any] = [_json_safe(item) for item in entries]
        return safe_list
    return value


def _coerce_updates(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Restore typed contract values (e.g. `ArtifactRef`) lost to JSON replay.

    A cached `NodeExecution` is read back as plain JSON, so nested contract
    models arrive as dicts. `OptimizationState`'s own field annotations are the
    source of truth for what each key should be, and pydantic's `Annotated`
    handling ignores the non-pydantic LangGraph reducer metadata already
    attached to those annotations.
    """

    coerced: dict[str, Any] = {}
    for key, value in updates.items():
        hint = _STATE_FIELD_TYPES.get(key)
        coerced[key] = TypeAdapter(hint).validate_python(value) if hint is not None else value
    return coerced


def _require(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"case state is missing required identity field {key!r}")
    return value


def _derive_idempotency_key(spec: NodeSpec, state: OptimizationState) -> str:
    """Default idempotency-key derivation for a node without bespoke logic.

    Real business nodes may need sharper keys (for example "request digest +
    source content digest" for A2 collectors); `idempotency_key_version` exists
    precisely so a future node can change its derivation without colliding
    with previously persisted intents.
    """

    return f"{_require(state, 'case_id')}:{spec.node_id}:{spec.idempotency_key_version}"


class NodeRuntime:
    """Fail-closed registry used by all LangGraph business-node wrappers.

    Implements the universal node execution protocol: precondition check,
    intent persistence, idempotency lookup, execute-or-reuse, artifact write,
    telemetry, then a validated checkpoint update. The intent/idempotency
    pipeline only applies to nodes whose `side_effect_class` is not `PURE` and
    only when the runtime is composed with real ports; `PURE` nodes are always
    recomputed, matching `NodeSpec.pure_nodes_do_not_retry`.

    Wall-clock timeout and automatic retry enforcement are deliberately not
    implemented here. `EXTERNAL_JOB` nodes delegate that to `WorkerBroker`,
    which carries `timeout_seconds` on `WorkerJob`; enforcing `NodeSpec.
    max_attempts` for in-process handlers is future work, not a FRAME-0 claim.
    """

    def __init__(
        self,
        registrations: Mapping[str, RegisteredNode] | None = None,
        *,
        ports: NodePorts | None = None,
    ) -> None:
        items = dict(registrations or {})
        mismatches = [
            key for key, registration in items.items() if key != registration.spec.node_id
        ]
        if mismatches:
            raise ValueError(f"registration keys do not match NodeSpec.node_id: {mismatches}")
        self._registrations = MappingProxyType(items)
        self._ports = ports

    @property
    def enabled_node_ids(self) -> frozenset[str]:
        return frozenset(self._registrations)

    def execute(self, node_id: str, state: OptimizationState) -> dict[str, Any]:
        registration = self._registrations.get(node_id)
        if registration is None:
            raise NodeNotEnabledError(
                f"business node {node_id} is present in topology but has no enabled handler"
            )

        spec = registration.spec
        if spec.side_effect_class is SideEffectClass.PURE or self._ports is None:
            execution = registration.handler(state, self._ports)
            self._validate_execution(node_id, spec, execution)
            return self._commit(node_id, execution)

        return self._execute_idempotent(node_id, registration, state)

    def _execute_idempotent(
        self, node_id: str, registration: RegisteredNode, state: OptimizationState
    ) -> dict[str, Any]:
        ports = self._ports
        if ports is None:  # pragma: no cover - guarded by caller
            raise RuntimeError("idempotent execution requires configured NodePorts")

        spec = registration.spec
        tenant_id = _require(state, "tenant_id")
        case_id = _require(state, "case_id")
        idempotency_key = _derive_idempotency_key(spec, state)
        digest_payload = _json_safe({"node_id": node_id, "state": state})
        input_digest = sha256_digest(canonical_json(digest_payload))

        existing = ports.intents.get(tenant_id=tenant_id, idempotency_key=idempotency_key)
        if (
            existing is not None
            and existing.status is IntentStatus.COMPLETED
            and existing.output_ref is not None
        ):
            payload = ports.artifacts.read(tenant_id=tenant_id, ref=existing.output_ref)
            execution = _load_execution(payload)
            return self._commit(node_id, execution)

        ports.intents.prepare(
            IntentRecord(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                node_id=node_id,
                case_id=case_id,
                status=IntentStatus.PENDING,
                input_digest=input_digest,
                updated_at=datetime.now(UTC),
            )
        )

        try:
            execution = registration.handler(state, ports)
        except Exception:
            ports.intents.mark_unknown(tenant_id=tenant_id, idempotency_key=idempotency_key)
            raise

        self._validate_execution(node_id, spec, execution)

        content = canonical_json(execution)
        output_ref = ports.artifacts.put_json(
            tenant_id=tenant_id,
            content=content,
            content_digest=sha256_digest(content),
            idempotency_key=idempotency_key,
        )
        ports.intents.complete(
            tenant_id=tenant_id, idempotency_key=idempotency_key, output_ref=output_ref
        )

        if ports.telemetry is not None:
            ports.telemetry.emit(
                TelemetryEvent(
                    case_id=case_id,
                    thread_id=state.get("thread_id", ""),
                    node_id=node_id,
                    attempt=1,
                    result_code=execution.route.value,
                    attributes={},
                )
            )

        return self._commit(node_id, execution)

    def _validate_execution(self, node_id: str, spec: NodeSpec, execution: NodeExecution) -> None:
        route = execution.route.value
        if route not in spec.allowed_routes:
            raise InvalidNodeExecutionError(
                f"business node {node_id} returned undeclared route {route!r}"
            )

        unknown_fields = set(execution.updates) - _RUNTIME_FIELDS
        if unknown_fields:
            raise InvalidNodeExecutionError(
                f"business node {node_id} returned unknown state fields: {sorted(unknown_fields)}"
            )
        protected_fields = set(execution.updates) & _IDENTITY_FIELDS
        if protected_fields:
            raise InvalidNodeExecutionError(
                f"business node {node_id} attempted to mutate identity fields: "
                f"{sorted(protected_fields)}"
            )

    def _commit(self, node_id: str, execution: NodeExecution) -> dict[str, Any]:
        updates = dict(execution.updates)
        updates["current_node"] = node_id
        updates["completed_nodes"] = [node_id]
        updates["node_routes"] = {node_id: execution.route.value}
        return updates


def _load_execution(payload: bytes) -> NodeExecution:
    raw = json.loads(payload)
    return NodeExecution(route=NodeRoute(raw["route"]), updates=_coerce_updates(raw["updates"]))


def node_callable(
    runtime: NodeRuntime, node_id: str
) -> Callable[[OptimizationState], dict[str, Any]]:
    """Create a separately named wrapper while keeping execution policy centralized."""

    def run(state: OptimizationState, /) -> dict[str, Any]:
        return runtime.execute(node_id, state)

    run.__name__ = node_id.replace(".", "_").lower()
    return run


def route_for(node_id: str) -> Callable[[OptimizationState], str]:
    def select(state: OptimizationState) -> str:
        routes = state.get("node_routes", {})
        return routes.get(node_id, NodeRoute.CONTINUE.value)

    return select
