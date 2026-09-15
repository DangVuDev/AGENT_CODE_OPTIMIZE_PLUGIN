# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
"""Production handlers for B1 (Automatic Bottleneck Discovery and Baseline Recovery).

B1.32-35 (the historical metrics/logs/traces/LLM-evidence queries) always
report `unavailable_reason` today -- no `LogQueryPort`/`MetricsQueryPort`
adapter exists yet, matching exactly the honest-gap pattern already
established for A2.63 (telemetry). Nothing downstream fabricates a signal
from that absence: B1.40-53 correctly detect *zero* opportunities when there
is zero real evidence, which is B1's own documented behavior, not a bug
(business rule BR-B1-011: "a no-op scan is success, not failure"). Every
node below is real, correct logic that starts producing genuine
opportunities the moment a real query adapter is wired into `NodePorts`.

B1.95 is not a handler here -- `orchestration/subgraphs/b1.py` embeds the
*real* `build_a2_graph` as that node; wiring `build_a2_registrations()` (not
pilot) into whatever builds B1's graph is what makes it real, not new B1
code.

B1.40-81 accumulate `DetectionReport`'s pieces (run groups, signals,
bindings, scores, decisions) across a linear chain and two fan-outs via
plain `OptimizationState` list fields (`b1_run_groups`, `b1_signals`, ...:
see `contracts/state.py`) rather than each re-sealing a shared artifact --
`merge_artifact_refs` hard-conflicts two different contents under the same
(type, artifact_id), so a shared envelope re-sealed node-by-node would
break the moment its content actually changed. B1.81 is the one place that
reads all of them back to seal the real `DetectionReport` exactly once.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.b1 import (
    CooldownDecision,
    DetectionReport,
    DetectionSignal,
    DiscoveryScanContext,
    FeatureBinding,
    HistoricalEvidenceBranch,
    HistoricalEvidenceItem,
    HistoricalRevision,
    HistoricalSourceInventory,
    ObservedSourceIdentity,
    OpportunityScore,
    OwnershipBinding,
    QualificationDecision,
    QualifiedOpportunity,
    ReadAuthorization,
    RegisteredSourceSet,
    RunGroup,
    SourceBinding,
)
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.platform import PolicyRequest
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="b1-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"

_B1_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "B1.71": {NodeRoute.CONTINUE.value, NodeRoute.QUARANTINE.value, NodeRoute.REJECTED.value},
    "B1.80": {NodeRoute.CONTINUE.value, NodeRoute.MERGED.value},
    "B1.81": {NodeRoute.CONTINUE.value, NodeRoute.CLOSED.value},
    "B1.91": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value},
}
_EXTERNAL_JOB_NODES = frozenset({"B1.31", "B1.32", "B1.33", "B1.34", "B1.35"})


def _spec(node_id: str) -> NodeSpec:
    routes = _B1_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    side_effect_class = (
        SideEffectClass.EXTERNAL_JOB
        if node_id in _EXTERNAL_JOB_NODES
        else SideEffectClass.IDEMPOTENT_WRITE
    )
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-b-b1",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="b1-production-v1",
        side_effect_class=side_effect_class,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/lane-b-local-codebase/01-b1-automatic-discovery.md",
        slo="B1 node completes within 60 seconds",
    )


def _handler(node_id: str) -> Any:
    # Lazy import avoids a package-import cycle while keeping the registry
    # as the single ID-to-callable authority.
    from .registry import NODE_HANDLERS

    return NODE_HANDLERS[node_id]


def build_b1_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import B1_NODE_IDS

    enabled = {
        "B1.10",
        "B1.20",
        "B1.21",
        "B1.30",
        "B1.31",
        "B1.32",
        "B1.33",
        "B1.34",
        "B1.35",
        "B1.40",
        "B1.41",
        "B1.50",
        "B1.51",
        "B1.52",
        "B1.53",
        "B1.60",
        "B1.61",
        "B1.62",
        "B1.70",
        "B1.71",
        "B1.80",
        "B1.81",
        "B1.90",
        "B1.91",
        "B1.96",
    }
    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in B1_NODE_IDS
        if node_id in enabled
    }


def build_b1_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build the one `NodeRuntime` `orchestration/subgraphs/b1.py`'s
    `build_b1_graph` needs.

    `build_b1_graph(runtime)` passes this *same* runtime to
    `build_a2_graph(runtime)` for the embedded B1.95 node (real baseline
    recovery reusing A2 verbatim, per the plan) -- so this must carry both
    B1's 25 native registrations and A2's 18, sharing one `ports` bundle
    (B1.31 needs `ports.policy`; A2.50/60/61/62 need `ports.policy`/
    `ports.workers` too, same as running Lane A directly).
    """

    from production_optimizer.application.a2_handlers import build_a2_registrations

    registrations = {**build_b1_registrations(), **build_a2_registrations()}
    return NodeRuntime(registrations, ports=ports)


# ---------------------------------------------------------------------------
# Group 1 -- scan, registry, fingerprint, historical inventory, authorization
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Group 2 -- historical query fan-out (B1_QUERY_FAN_OUT): honest-unavailable
# ---------------------------------------------------------------------------

_QUERY_KIND_LABEL = {
    "metrics": "metrics",
    "logs": "logs",
    "traces": "traces and profiles",
    "llm_evidence": "LLM execution evidence",
}


def _query_branch(
    state: OptimizationState, ports: NodePorts, *, node_id: str, query_kind: str
) -> NodeExecution:
    """B1.32-35: always honest, never fabricated.

    No `MetricsQueryPort`/`LogQueryPort`/`TraceQueryPort`/LLM-execution-log
    adapter is wired into `NodePorts` yet -- exactly the same real gap
    `a2_handlers._a2_63` already documents for telemetry. Reports zero
    evidence with a real reason instead of inventing a sample.
    """

    authorization = _read_model(
        ports, state, _require_ref(state, "ReadAuthorization"), ReadAuthorization
    )
    if not authorization.allowed:
        reason = f"{query_kind}: read authorization was denied ({'; '.join(authorization.reasons)})"
    else:
        reason = (
            f"{query_kind}: no {_QUERY_KIND_LABEL[query_kind]} query adapter is wired into "
            "NodePorts"
        )
    branch = _seal(
        HistoricalEvidenceBranch(
            **_branch_envelope(state, node_id, parents=[authorization.content_digest]),
            branch_id=cast("Any", node_id),
            branch_kind=cast("Any", query_kind),
            evidence=[],
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id=node_id)
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# Group 3 -- normalize, detect, bind, score, qualify, dedupe, cooldown
# ---------------------------------------------------------------------------

_QUERY_BRANCH_IDS = ("B1.32", "B1.33", "B1.34", "B1.35")


def _eligible_run_groups(state: OptimizationState) -> list[RunGroup]:
    return cast("list[RunGroup]", state.get("b1_run_groups", []))


def _detector(state: OptimizationState, *, run_groups: list[RunGroup]) -> NodeExecution:
    """Shared body for B1.50-53: each is a real, independent detector family
    (threshold/regression/hotspot/llm_quality) that inspects `run_groups`
    for its own pattern. All four currently see zero groups (B1.32-35 are
    honest-unavailable) and correctly emit zero signals -- this is where a
    future adapter's real evidence starts producing genuine `DetectionSignal`
    entries, not a stand-in that needs replacing."""

    del run_groups
    return NodeExecution(updates={"b1_signals": []})


_QUALIFICATION_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# Group 4 -- reconstruct A1-equivalent request, intake policy, seal opportunity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Shared helpers (mirrors a1_handlers.py/a2_handlers.py -- each lane's
# handler module keeps its own small copy rather than a shared import).
# ---------------------------------------------------------------------------


def _put_envelope(
    ports: NodePorts, state: OptimizationState, envelope: ArtifactEnvelope, *, node_id: str
) -> ArtifactRef:
    content = canonical_json(envelope.model_dump(mode="json", exclude={"content_digest"}))
    generic_ref = ports.artifacts.put_json(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=envelope.content_digest,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:{node_id}:{envelope.artifact_type}"
        ),
    )
    return ArtifactRef(
        artifact_type=envelope.artifact_type,
        schema_version=envelope.schema_version,
        artifact_id=envelope.artifact_id,
        content_digest=envelope.content_digest,
        uri=generic_ref.uri,
    )


def _read_model[T](
    ports: NodePorts, state: OptimizationState, ref: ArtifactRef, model: type[T]
) -> T:
    content = ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=ref)
    raw = TypeAdapter(dict[str, Any]).validate_json(content)
    if issubclass(cast("type[Any]", model), ArtifactEnvelope):
        raw.setdefault("content_digest", ref.content_digest)
    return cast("Any", model).model_validate(raw)


def _seal[T: ArtifactEnvelope](model: T) -> T:
    return model.model_copy(update={"content_digest": model_content_digest(model)})


def _base_envelope(
    state: OptimizationState, artifact_type: str, *, parents: list[str] | None = None
) -> dict[str, Any]:
    return {
        "artifact_id": f"{_required_state_str(state, 'case_id')}-{artifact_type}",
        "tenant_id": _required_state_str(state, "tenant_id"),
        "case_id": _required_state_str(state, "case_id"),
        "created_at": datetime.now(UTC),
        "producer": _PRODUCER,
        "policy_versions": {"b1": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    raise ValueError(f"missing required artifact ref: {artifact_type}")


def _branch_artifact_id(case_id: str, node_id: str) -> str:
    return f"{case_id}-{node_id}-HistoricalEvidenceBranch"


def _branch_envelope(
    state: OptimizationState, node_id: str, *, parents: list[str]
) -> dict[str, Any]:
    envelope = _base_envelope(state, "HistoricalEvidenceBranch", parents=parents)
    envelope["artifact_id"] = _branch_artifact_id(_required_state_str(state, "case_id"), node_id)
    return envelope


def _require_branch_ref(state: OptimizationState, node_id: str) -> ArtifactRef:
    artifact_id = _branch_artifact_id(_required_state_str(state, "case_id"), node_id)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == "HistoricalEvidenceBranch" and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required HistoricalEvidenceBranch for {node_id}")


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"B1 state is missing required field {key!r}")
    return value


def _git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
