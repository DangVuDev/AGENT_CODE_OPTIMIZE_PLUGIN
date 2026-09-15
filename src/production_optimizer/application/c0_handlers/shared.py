# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
"""Production handlers for C0 (shared lane convergence gate).

C0 sits after both `build_lane_a_graph` (post A3.90) and
`build_lane_b_proposal_graph` (post B2.60, see `orchestration/lanes.py`) --
its handlers are deliberately lane-agnostic: B1.95/B2.22 embed the *real*
`build_a2_graph`/`build_a3_graph`, so a Lane B run has already sealed the
exact same artifact types (`OptimizationRequest`, `SourceSnapshot`,
`BaselineSnapshot`, `EvidenceBundle`, `FindingSet`, `SolutionPortfolio`,
`A3QualityReport`) a Lane A run would. C0 never branches on `state["lane"]`
except at C0.10 (BR-C0-001: origin must match what was already sealed).

C0.10-50 each populate one plain `OptimizationState` field
(`c0_schema_results`/`c0_digest_chain`/`c0_equivalence_verdicts`/
`c0_freshness_checks`) with the exact leaf-type lists `ConvergenceDecision`
(`contracts/c0.py`) requires; C0.60 is the only node that assembles and
seals it, and C0.70 the only one that seals `ConvergedCase`.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import BaselineSnapshot, EvidenceBundle, SourceSnapshot
from production_optimizer.contracts.a3 import A3QualityReport, FindingSet, SolutionPortfolio
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.c0 import (
    ConvergedCase,
    ConvergenceDecision,
    DigestChainLink,
    DimensionEquivalenceVerdict,
    FreshnessCheck,
    SchemaValidationResult,
)
from production_optimizer.contracts.canonical import canonical_json, model_content_digest
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="c0-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_FRESHNESS_WINDOW = timedelta(days=7)

_REQUIRED_ARTIFACTS: tuple[tuple[str, type[ArtifactEnvelope]], ...] = (
    ("OptimizationRequest", OptimizationRequest),
    ("SourceSnapshot", SourceSnapshot),
    ("BaselineSnapshot", BaselineSnapshot),
    ("EvidenceBundle", EvidenceBundle),
    ("FindingSet", FindingSet),
    ("SolutionPortfolio", SolutionPortfolio),
    ("A3QualityReport", A3QualityReport),
)
_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = dict(_REQUIRED_ARTIFACTS)

# (child artifact type, digest field on the child, parent artifact type) --
# every `*_digest` linkage `contracts/a1.py`/`a2.py`/`a3.py` actually define
# between these seven stable, non-revision-pass-scoped artifact types. This
# deliberately excludes `A3QualityReport.solution_portfolio_digest`: that
# field is set (see `a3_handlers._a3_81`) to the *intermediate*
# `SolutionStrategySet` digest, not the final sealed `SolutionPortfolio` --
# an A3-internal bookkeeping detail, not one of the "request, source
# snapshot, evidence and solution references" BR-C0-002 names.
#
# `EvidenceBundle.baseline_digest` links to `SourceSnapshot`, not
# `BaselineSnapshot` -- confirmed against a real end-to-end run
# (`scripts/optimize.py`) and `a2_handlers._a2_80`'s own docstring: A2.80
# seals `EvidenceBundle` *before* `BaselineSnapshot` exists (A2.95 runs
# after it), so it cannot reference a not-yet-sealed artifact. In this
# `active_collection` baseline mode the source snapshot *is* the baseline
# being measured, which is exactly the digest `BaselineSnapshot.
# source_snapshot_digest` itself uses.
_DIGEST_LINKS: tuple[tuple[str, str, str], ...] = (
    ("BaselineSnapshot", "request_digest", "OptimizationRequest"),
    ("BaselineSnapshot", "source_snapshot_digest", "SourceSnapshot"),
    ("EvidenceBundle", "baseline_digest", "SourceSnapshot"),
    ("FindingSet", "evidence_bundle_digest", "EvidenceBundle"),
    ("SolutionPortfolio", "finding_set_digest", "FindingSet"),
    ("SolutionPortfolio", "quality_report_digest", "A3QualityReport"),
    ("A3QualityReport", "finding_set_digest", "FindingSet"),
)

_C0_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "C0.60": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _C0_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-convergence",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="c0-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/00-convergence.md",
        slo="C0 node completes within 60 seconds",
    )


def _handler(node_id: str) -> Any:
    # Lazy import avoids a package-import cycle while keeping the registry
    # as the single ID-to-callable authority.
    from .registry import NODE_HANDLERS

    return NODE_HANDLERS[node_id]


def build_c0_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import C0_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in C0_NODE_IDS
    }


def build_c0_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build the one `NodeRuntime` `orchestration/subgraphs/c0.py`'s
    `build_c0_graph` needs. Unlike B1/B2, C0 embeds no lane subgraph, so no
    registration merge is needed -- its 7 native nodes are the whole graph."""

    return NodeRuntime(build_c0_registrations(), ports=ports)


# ---------------------------------------------------------------------------
# Shared helpers (mirrors a2_handlers.py/b1_handlers.py/b2_handlers.py)
# ---------------------------------------------------------------------------


def _origin(state: OptimizationState) -> str:
    return "manual" if state.get("lane") == "manual" else "automatic"


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> BaseModel:
    ref = _require_ref(state, artifact_type)
    return _read_model(ports, state, ref, _MODEL_BY_TYPE[artifact_type])


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
        "policy_versions": {"c0": "production-v1"},
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
        raise ValueError(f"C0 state is missing required field {key!r}")
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


__all__ = ["build_c0_registrations", "build_c0_runtime"]
