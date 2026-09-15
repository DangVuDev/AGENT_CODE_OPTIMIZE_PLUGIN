# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
"""Shared state for S02 (Plan & Task List) -- turns S01's `SelectedSolution`
into an executable, phased plan (see
docs/project-blueprint/shared-workflow/02-plan-and-task-list.md).

S02.10/20 revalidate the treatment's scope against the pinned snapshot and
discover real dependent test files. S02.30 drafts phases+tasks via one real
model call (forced JSON, one-repair, mirrors `a3_handlers._generate_finding_drafts`
exactly); S02.40-70 validate/refine that draft with real, deterministic
checks into plain `s02_plan_draft` state (mirrors b2_handlers.py's
working-field pattern). S02.80 is a second, independent model call (critic,
`ModelRole.JUDGE`). S02.81 is the only node that seals `ExecutionPlan`/
`TaskList`/`PlanQualityReport` -- it never fabricates a plan/task-list digest
for a draft too broken to validly construct one (see `contracts.s02.
PlanQualityReport`'s docstring) -- and it is the only revision loop entry
(bounded, mirrors A3's `_MAX_REVISION_ATTEMPTS`). S02.90 is a pure approval
gate, real and resumable like S01.80, not B2.50/51's fail-closed-without-an-
interrupt shape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a2 import RepositoryManifest, SourceSnapshot
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, model_content_digest
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole
from production_optimizer.contracts.s01 import SelectedSolution
from production_optimizer.contracts.s02 import (
    ExecutionPhase,
    ExecutionPlan,
    PlanApproval,
    PlanQualityReport,
    PlanQualityResult,
    PlanTask,
    TaskList,
)
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="s02-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_POLICY_VERSION = "s02-plan-v1"
_PROMPT_VERSION = "s02-plan-v1"
_DEFAULT_S02_MODEL_ID = "claude-sonnet-5"
_MAX_S02_REVISIONS = 2
_APPROVAL_RISK_TIERS = {"code", "architecture"}

_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "S02.10": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S02.81": {NodeRoute.CONTINUE.value, NodeRoute.REVISION.value, NodeRoute.REJECTED.value},
    "S02.90": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value},
}

_PLAN_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "phase_id": {"type": "string"},
                    "sequence": {"type": "integer"},
                    "phase_kind": {"type": "string", "enum": ["diagnostic", "implementation"]},
                    "treatment": {
                        "type": "object",
                        "properties": {
                            "variable": {"type": "string"},
                            "before": {"type": "string"},
                            "after": {"type": "string"},
                        },
                        "required": ["variable", "before", "after"],
                    },
                    "done_criteria": {"type": "array", "items": {"type": "string"}},
                    "rollback_command": {"type": ["string", "null"]},
                    "rollback_trigger": {"type": "string"},
                    "rollback_deadline_seconds": {"type": "integer"},
                },
                "required": [
                    "phase_id",
                    "sequence",
                    "phase_kind",
                    "treatment",
                    "done_criteria",
                    "rollback_trigger",
                    "rollback_deadline_seconds",
                ],
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "phase_id": {"type": "string"},
                    "objective": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "proposed_creation": {"type": "boolean"},
                    "instructions": {"type": "string"},
                    "owner": {"type": "string"},
                },
                "required": ["task_id", "phase_id", "objective", "instructions", "owner"],
            },
        },
    },
    "required": ["phases", "tasks"],
}

_CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "omissions": {"type": "array", "items": {"type": "string"}},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "approved": {"type": "boolean"},
    },
    "required": ["omissions", "concerns", "approved"],
}


def _model_id(ports: NodePorts) -> str:
    return ports.model_id or _DEFAULT_S02_MODEL_ID


def build_s02_runtime(*, ports: NodePorts) -> NodeRuntime:
    return NodeRuntime(build_s02_registrations(), ports=ports)


def build_s02_registrations() -> dict[str, RegisteredNode]:
    return build_bound_s02_registrations()


def _spec(node_id: str) -> NodeSpec:
    routes = _ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-workflow-s02",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="s02-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=120,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/02-plan-and-task-list.md",
        slo="S02 node completes within 10 minutes excluding human wait",
    )


def _handler(node_id: str) -> Any:
    # Lazy import avoids a package-import cycle while keeping the registry
    # as the single ID-to-callable authority.
    from .registry import NODE_HANDLERS

    return NODE_HANDLERS[node_id]


def build_bound_s02_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import S02_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in S02_NODE_IDS
    }


def _strategy_by_id(portfolio: SolutionPortfolio, strategy_id: str) -> Any:
    for strategy in portfolio.strategies:
        if strategy.strategy_id == strategy_id:
            return strategy
    raise ValueError(f"strategy {strategy_id!r} not found in SolutionPortfolio")


def _build_plan_context(strategy: Any, dependency_map: dict[str, list[str]]) -> str:
    lines = [
        f"Strategy: {strategy.title}",
        f"Mechanism: {strategy.mechanism}",
        f"Risk ceiling: {strategy.risk_ceiling}",
        "\nExisting phase templates (refine/extend, keep sequence order):",
    ]
    for template in strategy.phase_templates:
        lines.append(
            f"- {template.phase_id} (seq={template.sequence}, kind={template.phase_kind}): "
            f"{template.treatment.variable} {template.treatment.before} -> "
            f"{template.treatment.after}"
        )
    lines.append("\nCriteria this strategy claims to affect (must be covered by done_criteria):")
    for impact in strategy.impact_assessment.criterion_impacts:
        lines.append(
            f"- {impact.criterion_id} ({impact.direction}, confidence={impact.confidence})"
        )
    lines.append("\nScope entries and any real test files discovered for them:")
    for entry in strategy.scope_resolution.entries:
        tests = dependency_map.get(entry.path_or_symbol, [])
        lines.append(f"- {entry.path_or_symbol} ({entry.kind}) tests={tests}")
    return "\n".join(lines)


def _one_repair_complete(
    ports: NodePorts, request: ModelCompletionRequest
) -> tuple[dict[str, Any] | None, int]:
    """Shared one-bounded-repair completion loop -- mirrors
    `a3_handlers._generate_finding_drafts`'s exact retry shape."""

    assert ports.model is not None
    result = ports.model.complete(request)
    tokens = result.input_tokens + result.output_tokens
    if not result.valid_json:
        repair = request.model_copy(
            update={
                "messages": [
                    *request.messages,
                    ModelMessage(
                        role="user",
                        content=(
                            "Your previous reply did not match the required JSON "
                            "schema. Reply again with only a valid tool call "
                            "matching the schema."
                        ),
                    ),
                ],
                "idempotency_key": f"{request.idempotency_key}:repair-1",
            }
        )
        result = ports.model.complete(repair)
        tokens += result.input_tokens + result.output_tokens
    if not result.valid_json or result.parsed_json is None:
        return None, tokens
    return result.parsed_json, tokens


def _check_acyclic(tasks: list[PlanTask]) -> tuple[bool, str | None]:
    graph = {task.task_id: task.depends_on for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> bool:
        if task_id in visited:
            return True
        if task_id in visiting:
            return False
        visiting.add(task_id)
        for dep in graph.get(task_id, []):
            if dep in graph and not visit(dep):
                return False
        visiting.discard(task_id)
        visited.add(task_id)
        return True

    for task_id in graph:
        if not visit(task_id):
            return False, f"dependency cycle detected involving {task_id!r}"
    return True, None


def _check_phase_risk_order(phases: list[ExecutionPhase]) -> tuple[bool, str | None]:
    """S02.40's own contract ("ordered by dependency and risk") plus
    `BR-02-006` ("diagnostic phases ... cannot authorize production
    implementation") together mean cheap, reversible investigation must
    always land before an expensive, code-changing phase spends its risk
    budget -- never the other way around and never interleaved past the
    first implementation phase. `phase_kind` is the only risk signal a
    phase carries (A3 assigns `risk_ceiling` once, per strategy, not per
    phase), so the deterministic rule this gate enforces is exactly:
    every 'diagnostic' phase's `sequence` must be lower than every
    'implementation' phase's `sequence`. A strategy with only one kind of
    phase has nothing to order, so it trivially passes.
    """

    diagnostic_sequences = [p.sequence for p in phases if p.phase_kind == "diagnostic"]
    implementation_sequences = [p.sequence for p in phases if p.phase_kind == "implementation"]
    if not diagnostic_sequences or not implementation_sequences:
        return True, None
    if max(diagnostic_sequences) < min(implementation_sequences):
        return True, None
    return False, (
        f"diagnostic phase sequence(s) {sorted(diagnostic_sequences)} do not all precede "
        f"implementation phase sequence(s) {sorted(implementation_sequences)} -- cheap, "
        "reversible diagnostic work must land before any implementation phase runs"
    )


# ---------------------------------------------------------------------------
# Shared helpers (mirrors s01_handlers.py/c0_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "SelectedSolution": cast("type[ArtifactEnvelope]", SelectedSolution),
    "SolutionPortfolio": cast("type[ArtifactEnvelope]", SolutionPortfolio),
    "SourceSnapshot": cast("type[ArtifactEnvelope]", SourceSnapshot),
    "RepositoryManifest": cast("type[ArtifactEnvelope]", RepositoryManifest),
}


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> Any:
    ref = _require_ref(state, artifact_type)
    return _read_model(ports, state, ref, _MODEL_BY_TYPE[artifact_type])


def _pass_stage_id(node_id: str, pass_number: int) -> str:
    return f"{node_id}-pass{pass_number}"


def _stage_artifact_id(case_id: str, node_id: str, artifact_type: str) -> str:
    return f"{case_id}-{node_id}-{artifact_type}"


def _s01_pass_number(state: OptimizationState) -> int:
    """Mirrors `s01_handlers._s01_pass_number` exactly -- `RankingResult`/
    `SelectedSolution` are pass-scoped there (BR-01-005: a REVERT sends the
    case back to S01 with one more `s01_excluded_strategy_ids` entry, a real
    second pass whose different content would otherwise collide under
    `merge_artifact_refs` with the first pass's already-recorded refs). S02
    must resolve the *same* pass's `SelectedSolution`, not whichever one a
    plain first-match-by-type scan happens to find first in the sorted
    `artifact_refs` list (which is the stale one)."""

    return len(cast("list[str]", state.get("s01_excluded_strategy_ids") or []))


def _selected_solution_ref(state: OptimizationState) -> ArtifactRef:
    node_id = f"S01.90-pass{_s01_pass_number(state)}"
    artifact_id = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, "SelectedSolution"
    )
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == "SelectedSolution" and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required SelectedSolution produced by {node_id}")


def _read_selected_solution(ports: NodePorts, state: OptimizationState) -> SelectedSolution:
    return _read_model(ports, state, _selected_solution_ref(state), SelectedSolution)


def _stage_envelope(state: OptimizationState, node_id: str, artifact_type: str) -> dict[str, Any]:
    """`_base_envelope` with a node(+pass)-scoped `artifact_id` -- mirrors
    `a3_handlers._stage_envelope` exactly; see the S02.81 node's comment for
    why `PlanQualityReport` needs this and `ExecutionPlan`/`TaskList` don't.
    """

    envelope = _base_envelope(state, artifact_type)
    envelope["artifact_id"] = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, artifact_type
    )
    return envelope


def _put_envelope(
    ports: NodePorts, state: OptimizationState, envelope: ArtifactEnvelope, *, node_id: str
) -> ArtifactRef:
    content = canonical_json(envelope.model_dump(mode="json", exclude={"content_digest"}))
    generic_ref = ports.artifacts.put_json(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=envelope.content_digest,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:{node_id}:"
            f"{envelope.artifact_type}:{envelope.artifact_id}"
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
        "policy_versions": {"s02": "production-v1"},
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
        raise ValueError(f"S02 state is missing required field {key!r}")
    return value


__all__ = ["build_bound_s02_registrations", "build_s02_registrations", "build_s02_runtime"]
