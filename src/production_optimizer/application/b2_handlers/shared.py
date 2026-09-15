# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
"""Production handlers for B2 (Automatic Grounded Proposal).

B2.22 is not a handler here -- `orchestration/subgraphs/b2.py` embeds the
*real* `build_a3_graph` as that node; `build_b2_runtime()` merges B2's
native registrations with A3's real ones (`build_a3_registrations()`),
mirroring exactly how `build_b1_runtime()` merges B1 with A2.

Several B2 contracts (`AnalysisStrategy`, `ModelContextPackage`,
`DiscoveryAssumptionReport`, `StalenessDecision`) are deliberately plain
`ContractModel`, not `ArtifactEnvelope` -- they are working inputs to
B2.22's embedded A3 run and B2.60's final `ProposalEnvelope` assembly, not
independently-referenced lane artifacts, so they travel through plain
`OptimizationState` fields (`b2_*`, see `contracts/state.py`) rather than
the artifact store. `ProposalEnvelope` itself is sealed exactly once, at
B2.60, once its required `routing_decision`/`approval` fields are actually
known -- sealing it earlier (e.g. at B2.40) and re-sealing it later would
hit the same `merge_artifact_refs` conflict B1.40-81 avoids by not
re-sealing `DetectionReport` node-by-node.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
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
from production_optimizer.contracts.a2 import EvidenceBundle, RepositoryManifest
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.b1 import (
    ObservedSourceIdentity,
    QualifiedOpportunity,
    RegisteredSourceSet,
)
from production_optimizer.contracts.b2 import (
    AnalysisStrategy,
    DiscoveryAssumption,
    DiscoveryAssumptionReport,
    ModelContextPackage,
    ProposalApproval,
    ProposalEnvelope,
    ProposalRoutingDecision,
    StalenessDecision,
    TruncationReport,
)
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="b2-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"

_B2_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "B2.31": {NodeRoute.CONTINUE.value, NodeRoute.REFRESH.value, NodeRoute.REJECTED.value},
    "B2.50": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value},
    "B2.52": {NodeRoute.CONTINUE.value, NodeRoute.REVISION.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _B2_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-b-b2",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="b2-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/lane-b-local-codebase/02-b2-automatic-proposal.md",
        slo="B2 node completes within 60 seconds",
    )


def _handler(node_id: str) -> Any:
    # Lazy import avoids a package-import cycle while keeping the registry
    # as the single ID-to-callable authority.
    from .registry import NODE_HANDLERS

    return NODE_HANDLERS[node_id]


def build_b2_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import B2_NODE_IDS

    enabled = {
        "B2.10",
        "B2.20",
        "B2.21",
        "B2.30",
        "B2.31",
        "B2.40",
        "B2.41",
        "B2.50",
        "B2.51",
        "B2.52",
        "B2.60",
    }
    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in B2_NODE_IDS
        if node_id in enabled
    }


def build_b2_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build the one `NodeRuntime` `orchestration/subgraphs/b2.py`'s
    `build_b2_graph` needs -- B2's 11 native registrations plus A3's 22 real
    ones, sharing one `ports` bundle, same pattern as `build_b1_runtime`."""

    from production_optimizer.application.a3_handlers import build_a3_registrations

    registrations = {**build_b2_registrations(), **build_a3_registrations()}
    return NodeRuntime(registrations, ports=ports)


# ---------------------------------------------------------------------------
# Shared helpers (mirrors a2_handlers.py/b1_handlers.py)
# ---------------------------------------------------------------------------


class _OptimizationRequestLike:
    """Minimal read shape for B2.21's `criteria_ids` -- avoids importing all
    of `contracts.a1.OptimizationRequest`'s nested leaf types just to read
    one field; `_read_model` only needs `model_validate`/attribute access."""

    def __init__(self, criteria: list[dict[str, Any]]) -> None:
        self.criteria = criteria

    @classmethod
    def model_validate(cls, raw: dict[str, Any]) -> _OptimizationRequestLike:
        return cls(criteria=cast("list[dict[str, Any]]", raw.get("criteria", [])))


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
        "policy_versions": {"b2": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    ref = _try_ref(state, artifact_type)
    if ref is None:
        raise ValueError(f"missing required artifact ref: {artifact_type}")
    return ref


def _try_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef | None:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    return None


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"B2 state is missing required field {key!r}")
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
