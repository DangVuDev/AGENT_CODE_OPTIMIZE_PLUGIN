# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
"""Shared state for S01 (Rank & Select) -- the first shared-workflow step
after C0, consuming a `ConvergedCase`'s `SolutionPortfolio` and
`OptimizationRequest` (see
docs/project-blueprint/shared-workflow/01-rank-and-select.md).

S01.10-70 accumulate real, deterministic ranking data into plain `s01_*`
state fields (mirrors b2_handlers.py's working-field pattern, no LLM
anywhere in this stage). S01.80 is the only node that halts for human
approval: it seals `RankingResult` and, when needed, sets a real
`InterruptEnvelope` on `pending_interrupt` so `application.resume.resume_case`
can later authorize an actual resume -- unlike B2.50/51's shape (which fails
closed to REJECTED without ever creating a resumable interrupt), this
mirrors `a1_handlers`'s A1.90 pattern, which is genuinely resumable. S01.90
seals `SelectedSolution` once approval is secured.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, model_content_digest
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.s01 import (
    RankingResult,
    SelectedSolution,
    SelectionApproval,
    StrategyScore,
)
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="s01-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_POLICY_VERSION = "s01-ranking-v1"

# A first real, versioned scoring formula (benefit dominates; effort,
# reversibility and risk are real but secondary tie-breakers) -- same "pick
# real weights, document them, version the policy string" convention already
# used for B1.70's severity/confidence score.
_BENEFIT_WEIGHT = 0.55
_EFFORT_WEIGHT = 0.2
_REVERSIBILITY_WEIGHT = 0.15
_RISK_WEIGHT = 0.1
_MATERIAL_TIE_MARGIN = 0.05
_SENSITIVITY_PERTURBATION = 1.5
_RISK_TIER_ORDER = {"experiment_config": 0, "prompt": 1, "code": 2, "architecture": 3}
_EFFORT_SCALE = {"low": 1.0, "medium": 0.6, "high": 0.3}
_REVERSIBILITY_SCALE = {"instant": 1.0, "fast": 0.75, "slow": 0.4, "irreversible": 0.0}
_APPROVAL_RISK_TIERS = {"code", "architecture"}
# BR-01-004 also requires human approval for "low-confidence" selections, not
# just high-risk/close-call ones -- A3.62's own docstring documents that
# `impact_assessment.criterion_impacts[].confidence` is a deliberately
# conservative placeholder (`confidence=0.0, basis="forecast"`) whenever no
# real execution telemetry backs it, so a winner whose average confidence
# across its "improves" impacts falls below this bar must not auto-select.
_LOW_CONFIDENCE_THRESHOLD = 0.5

_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "S01.20": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S01.80": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value},
}


def build_s01_runtime(*, ports: NodePorts) -> NodeRuntime:
    return NodeRuntime(build_s01_registrations(), ports=ports)


def build_s01_registrations() -> dict[str, RegisteredNode]:
    return build_bound_s01_registrations()


def _spec(node_id: str) -> NodeSpec:
    routes = _ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-workflow-s01",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="s01-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=30,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/01-rank-and-select.md",
        slo="S01 node completes within 30 seconds excluding human wait",
    )


def _handler(node_id: str) -> Any:
    # Lazy import avoids a package-import cycle while keeping the registry
    # as the single ID-to-callable authority.
    from .registry import NODE_HANDLERS

    return NODE_HANDLERS[node_id]


def build_bound_s01_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import S01_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in S01_NODE_IDS
    }


def _rank(scores: list[StrategyScore]) -> list[str]:
    remaining = sorted(scores, key=lambda score: score.total_score, reverse=True)
    result: list[str] = []
    while remaining:
        top_score = remaining[0].total_score
        tied = [s for s in remaining if top_score - s.total_score <= _MATERIAL_TIE_MARGIN]
        winner = min(tied, key=lambda s: _RISK_TIER_ORDER[s.risk_tier])
        result.append(winner.strategy_id)
        remaining = [s for s in remaining if s.strategy_id != winner.strategy_id]
    return result


def _perturb_score(score: StrategyScore, criterion_id: str, multiplier: float) -> StrategyScore:
    contribution = score.criterion_scores.get(criterion_id)
    if contribution is None:
        return score
    delta = contribution * (multiplier - 1.0)
    return score.model_copy(update={"total_score": score.total_score + delta})


# ---------------------------------------------------------------------------
# Shared helpers (mirrors b2_handlers.py/c0_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> Any:
    ref = _require_ref(state, artifact_type)
    model_by_type: dict[str, type[ArtifactEnvelope]] = {
        "OptimizationRequest": cast("type[ArtifactEnvelope]", OptimizationRequest),
        "SolutionPortfolio": cast("type[ArtifactEnvelope]", SolutionPortfolio),
    }
    return _read_model(ports, state, ref, model_by_type[artifact_type])


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
        "created_at": _now(),
        "producer": _PRODUCER,
        "policy_versions": {"s01": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _s01_pass_number(state: OptimizationState) -> int:
    """0 on S01's first pass in this case; N after N real S06 REVERTs.

    `s01_excluded_strategy_ids` grows by exactly one entry per REVERT
    (`s06_handlers._s06_80`), so its length is already a correct, natural
    pass counter -- no separate `s01_revision_attempts` state field needed.
    """

    return len(cast("list[str]", state.get("s01_excluded_strategy_ids") or []))


def _pass_stage_id(node_id: str, pass_number: int) -> str:
    return f"{node_id}-pass{pass_number}"


def _stage_artifact_id(case_id: str, node_id: str, artifact_type: str) -> str:
    return f"{case_id}-{node_id}-{artifact_type}"


def _stage_envelope(
    state: OptimizationState, node_id: str, artifact_type: str, *, parents: list[str]
) -> dict[str, Any]:
    """`_base_envelope` with a node-scoped `artifact_id` -- see the S01.80
    node's docstring for why S01's REVERT-driven re-entry needs this."""

    envelope = _base_envelope(state, artifact_type, parents=parents)
    envelope["artifact_id"] = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, artifact_type
    )
    return envelope


def _require_stage_ref(state: OptimizationState, node_id: str, artifact_type: str) -> ArtifactRef:
    artifact_id = _stage_artifact_id(_required_state_str(state, "case_id"), node_id, artifact_type)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required {artifact_type} produced by {node_id}")


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
        raise ValueError(f"S01 state is missing required field {key!r}")
    return value


__all__ = ["build_bound_s01_registrations", "build_s01_registrations", "build_s01_runtime"]
