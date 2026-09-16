from __future__ import annotations

import json
import os
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
from production_optimizer.ports.evidence_decoder import EvidenceDecoderPort
from production_optimizer.ports.intents import IntentLedger
from production_optimizer.ports.model_provider import ModelProviderPort
from production_optimizer.ports.policy import PolicyPort
from production_optimizer.ports.registries import RegistryPort
from production_optimizer.ports.secrets import SecretsBroker
from production_optimizer.ports.source_provider import SourceProviderPort
from production_optimizer.ports.telemetry import TelemetryPort
from production_optimizer.ports.workers import WorkerBroker

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

    `policy`, `secrets`, `workers`, `registry` and `model` are optional
    because not every lane's nodes need every capability. A handler that
    needs one of these must check for `None` and refuse to run rather than
    fabricate the capability — the same fail-closed rule `NodeRuntime`
    itself applies to an unregistered node. `model` backs A3's
    generator/judge nodes (see `ports/model_provider.py`); ADR-0002 records
    that decision. `model_id` is the provider-specific model name to request
    on that port (e.g. an Anthropic model alias vs. a local Ollama tag) --
    each `ModelProviderPort` implementation accepts whatever its own backend
    understands, so this must match whichever `model` is actually wired in.
    """

    artifacts: ArtifactStore
    intents: IntentLedger
    telemetry: TelemetryPort | None = None
    policy: PolicyPort | None = None
    secrets: SecretsBroker | None = None
    workers: WorkerBroker | None = None
    registry: RegistryPort | None = None
    model: ModelProviderPort | None = None
    model_id: str | None = None
    sources: SourceProviderPort | None = None
    evidence_decoders: EvidenceDecoderPort | None = None


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
_TRACE_NODE_OUTPUTS_ENV = "OPTIMIZER_TRACE_NODE_OUTPUTS"
_TRACE_NODE_OUTPUT_MAX_BYTES_ENV = "OPTIMIZER_TRACE_NODE_OUTPUT_MAX_BYTES"
_DEFAULT_TRACE_NODE_OUTPUT_MAX_BYTES = 12000


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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _trace_output_max_bytes() -> int:
    raw = os.environ.get(_TRACE_NODE_OUTPUT_MAX_BYTES_ENV, "").strip()
    if not raw:
        return _DEFAULT_TRACE_NODE_OUTPUT_MAX_BYTES
    try:
        return max(1000, int(raw))
    except ValueError:
        return _DEFAULT_TRACE_NODE_OUTPUT_MAX_BYTES


def _print_node_output(node_id: str, route: NodeRoute, updates: Mapping[str, Any]) -> None:
    payload = {
        "node_id": node_id,
        "route": route.value,
        "updates": _json_safe(dict(updates)),
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True, default=str)
    max_bytes = _trace_output_max_bytes()
    encoded = rendered.encode("utf-8")
    if len(encoded) > max_bytes:
        rendered = encoded[:max_bytes].decode("utf-8", errors="ignore")
        rendered = f"{rendered}\n... [truncated at {max_bytes} bytes]"
    print(f"\n[node-output] {node_id} route={route.value}")
    print(rendered)


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

    A3's `A3.81`<->`A3.82`<->`A3.60` revision loop (`orchestration/subgraphs/
    a3.py`), S02's `S02.81`<->`S02.30` redraft loop
    (`orchestration/subgraphs/s02.py`), and the shared `S03`<->`S04` (and,
    from S06.50, `S06`<->`S03`) implementation-repair loop
    (`orchestration/shared_workflow.py`) can each revisit the same `node_id`
    multiple times for one case with genuinely different semantic input (a
    new revision pass) — the base key above cannot distinguish those calls,
    so `_execute_idempotent` would replay pass 0's cached result forever
    instead of re-executing. Appending the revision counter *only when it is
    nonzero* fixes that while leaving every pass-0 key (and every lane that
    never sets any of these fields, i.e. A1/A2/B1/B2/C0/S01) byte-identical
    to before — no existing idempotency key format changes. The three
    counters never more than one applies to the same case (each lives only
    inside its own stage's loop), so checking all three unconditionally is
    safe.

    `resume_attempts` (see `application.resume.resume_case`) is the same
    fix for a different replay: a node that halted with `pending_interrupt`
    (e.g. A1.90's approval route) already has a COMPLETED intent recording
    that halt, so re-invoking the graph as-is would just replay it forever
    instead of letting the handler see the now-resolved `resume_command` and
    pick a different route. Scoped to `resume_target_node` (the one node
    that actually halted) so every other, already-completed node in the
    graph still cache-hits on resume instead of recomputing -- recomputing
    would give it a fresh `created_at` and a genuinely different digest for
    an artifact_id LangGraph's `merge_artifact_refs` reducer already saw,
    which surfaces as a hard conflict, not silent divergence.
    """

    base = f"{_require(state, 'case_id')}:{spec.node_id}:{spec.idempotency_key_version}"
    for field in ("a3_revision_attempts", "s02_revision_attempts", "s03_revision_attempts"):
        revision_pass = state.get(field, 0)  # type: ignore[literal-required]
        if revision_pass:
            base = f"{base}:rev{revision_pass}"
    resume_attempt = state.get("resume_attempts", 0)
    if resume_attempt and state.get("resume_target_node") == spec.node_id:
        base = f"{base}:resume{resume_attempt}"
    return base


def _next_route_key(node_id: str, existing_routes: Mapping[str, str], route: str) -> str:
    """Pick the key `NodeRuntime._commit` should record this route under.

    A3's `A3.81`<->`A3.82`<->`A3.60` revision loop and S02's
    `S02.81`<->`S02.30` redraft loop can each revisit the same `node_id`
    within one `graph.invoke()` call and legitimately produce a *different*
    route each pass (e.g. "revision" then "rejected") -- `merge_node_routes`
    (`contracts/state.py`) correctly treats two different values under the
    same plain `node_id` key as a hard conflict, since ordinarily that would
    mean nondeterministic replay.

    A revisit with the *same* route as last time is not that case -- it is
    the ordinary `application.resume.resume_case` shape, which re-invokes
    the whole graph from `START` so every already-completed node upstream of
    the halted one legitimately "reruns" (cache-hit, identical route) on
    every resume. `merge_node_routes` already tolerates that (equal values
    never conflict), so reusing the plain `node_id` key there keeps
    `node_routes` from growing a new numbered entry on every resume of every
    upstream node -- only an actual value change claims a new slot.

    Keyed off `node_routes`'s *own* accumulated history (`existing_routes`,
    the state as of just before this node runs) rather than either revision
    counter: a counter-based suffix looks appealingly simple, but the
    counter's value at write time (before this node's own contribution is
    merged) and at `route_for`'s read time (after) disagree by exactly this
    node's own increment, which silently mis-keys the very first repeat
    visit. Counting *this node_id's own* prior appearances instead needs no
    such synchronization: the first-ever visit (or a same-value replay)
    always resolves to the plain `node_id`, and each new value claims the
    next free `"{node_id}#N"` slot purely from what's already recorded, so
    `route_for` can independently rediscover the same key by walking the
    same numbering.
    """

    if node_id not in existing_routes:
        return node_id
    key = _latest_route_key(node_id, existing_routes)
    assert key is not None
    if existing_routes[key] == route:
        return key
    n = 2
    while f"{node_id}#{n}" in existing_routes:
        n += 1
    return f"{node_id}#{n}"


def _latest_route_key(node_id: str, routes: Mapping[str, str]) -> str | None:
    """The counterpart lookup to `_next_route_key`: the highest-numbered
    key recorded for `node_id`, or `None` if it never ran."""

    if node_id not in routes:
        return None
    key = node_id
    n = 2
    while f"{node_id}#{n}" in routes:
        key = f"{node_id}#{n}"
        n += 1
    return key


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
            return self._commit(node_id, execution, state)

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
            return self._commit(node_id, execution, state)

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

        return self._commit(node_id, execution, state)

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

    def _commit(
        self, node_id: str, execution: NodeExecution, state: OptimizationState
    ) -> dict[str, Any]:
        updates = dict(execution.updates)
        updates["current_node"] = node_id
        updates["completed_nodes"] = [node_id]
        route_key = _next_route_key(node_id, state.get("node_routes", {}), execution.route.value)
        updates["node_routes"] = {route_key: execution.route.value}
        if _env_flag(_TRACE_NODE_OUTPUTS_ENV):
            _print_node_output(node_id, execution.route, updates)
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
        route_key = _latest_route_key(node_id, routes)
        if route_key is None:
            return NodeRoute.CONTINUE.value
        return routes[route_key]

    return select
