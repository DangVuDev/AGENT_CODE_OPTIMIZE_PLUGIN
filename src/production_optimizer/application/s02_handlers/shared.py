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

import re
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
from production_optimizer.contracts.a1 import OptimizationRequest
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
_RISK_TIER_ORDER = {"experiment_config": 0, "prompt": 1, "code": 2, "architecture": 3}
_PATH_PROMPT_LIMIT = 200
_PATH_SCAN_LIMIT = 5000
_IGNORED_PATH_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
_LOW_VALUE_TOKENS = {
    "and",
    "app",
    "code",
    "file",
    "for",
    "from",
    "http",
    "impl",
    "into",
    "latency",
    "metric",
    "optimize",
    "performance",
    "primary",
    "service",
    "test",
    "that",
    "the",
    "this",
    "with",
}

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
                    "risk_tier": {
                        "type": "string",
                        "enum": ["experiment_config", "prompt", "code", "architecture"],
                    },
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
                    "affected_criteria": {"type": "array", "items": {"type": "string"}},
                    "validation_command_ids": {"type": "array", "items": {"type": "string"}},
                    "rollback_command": {"type": ["string", "null"]},
                    "rollback_trigger": {"type": "string"},
                    "rollback_deadline_seconds": {"type": "integer"},
                },
                "required": [
                    "phase_id",
                    "sequence",
                    "phase_kind",
                    "risk_tier",
                    "treatment",
                    "done_criteria",
                    "affected_criteria",
                    "validation_command_ids",
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


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _repository_file_paths(snapshot: SourceSnapshot) -> list[str]:
    snapshot_paths = sorted(
        {
            _normalize_repo_path(file.relative_path)
            for file in snapshot.files
            if file.relative_path.strip()
        }
    )
    if snapshot_paths:
        return snapshot_paths

    root = Path(snapshot.canonical_path_ref)
    if not root.exists():
        return []

    paths: list[str] = []
    try:
        for candidate in root.rglob("*"):
            if len(paths) >= _PATH_SCAN_LIMIT:
                break
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root)
            if any(part in _IGNORED_PATH_PARTS for part in relative.parts):
                continue
            paths.append(_normalize_repo_path(str(relative)))
    except OSError:
        return sorted(paths)
    return sorted(paths)


def _text_tokens(*parts: str | None) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        if not part:
            continue
        for token in re.findall(r"[A-Za-z0-9_]+", part.lower()):
            normalized = token.strip("_")
            if len(normalized) >= 3 and normalized not in _LOW_VALUE_TOKENS:
                tokens.add(normalized)
    return tokens


def _strategy_relevance_tokens(strategy: Any, request: OptimizationRequest | None) -> set[str]:
    text_parts: list[str | None] = [strategy.title, strategy.mechanism, strategy.strategy_tradeoffs]
    for template in strategy.phase_templates:
        text_parts.extend(
            [
                template.phase_id,
                template.treatment.variable,
                template.treatment.before,
                template.treatment.after,
            ]
        )
    for entry in strategy.scope_resolution.entries:
        text_parts.append(entry.path_or_symbol)
    if request is not None:
        text_parts.extend(
            [
                request.objective.feature_id,
                request.objective.statement,
                request.workload.workload_id,
            ]
        )
        text_parts.extend(criterion.metric_id for criterion in request.criteria)
        text_parts.extend(guardrail.metric_id for guardrail in request.guardrails)
    return _text_tokens(*text_parts)


def _rank_paths_by_relevance(paths: list[str], tokens: set[str]) -> list[str]:
    if not tokens:
        return paths

    def score(path: str) -> tuple[int, str]:
        lower_path = path.lower()
        basename = path.rsplit("/", maxsplit=1)[-1].lower()
        value = 0
        for token in tokens:
            if token in basename:
                value += 4
            if token in lower_path:
                value += 1
        return (-value, path)

    return sorted(paths, key=score)


def _allowed_plan_paths(
    strategy: Any,
    dependency_map: dict[str, list[str]],
    snapshot: SourceSnapshot,
    request: OptimizationRequest | None,
) -> list[str]:
    paths = _repository_file_paths(snapshot)
    if len(paths) <= _PATH_PROMPT_LIMIT:
        return paths

    known = set(paths)
    pinned: set[str] = set()
    for entry in strategy.scope_resolution.entries:
        normalized = _normalize_repo_path(entry.path_or_symbol)
        if normalized in known:
            pinned.add(normalized)
    for source_path, tests in dependency_map.items():
        normalized_source = _normalize_repo_path(source_path)
        if normalized_source in known:
            pinned.add(normalized_source)
        for test_path in tests:
            normalized_test = _normalize_repo_path(test_path)
            if normalized_test in known:
                pinned.add(normalized_test)

    ranked = _rank_paths_by_relevance(paths, _strategy_relevance_tokens(strategy, request))
    selected: list[str] = []
    for path in [*sorted(pinned), *ranked]:
        if path not in selected:
            selected.append(path)
        if len(selected) >= _PATH_PROMPT_LIMIT:
            break
    return selected


def _format_request_criteria(request: OptimizationRequest | None) -> list[str]:
    if request is None:
        return ["- <OptimizationRequest unavailable to S02>"]
    lines: list[str] = []
    for criterion in request.criteria:
        lines.append(
            "- "
            f"{criterion.criterion_id}: metric={criterion.metric_id}, "
            f"aggregation={criterion.aggregation}, direction={criterion.direction}, "
            f"target={criterion.target}{criterion.unit}, "
            f"acceptance={criterion.acceptance_operator}, weight={criterion.weight}"
        )
    if request.guardrails:
        lines.append("Guardrails that implementation must preserve:")
        for guardrail in request.guardrails:
            lines.append(
                "- "
                f"{guardrail.guardrail_id}: metric={guardrail.metric_id}, "
                f"operator={guardrail.operator}, threshold={guardrail.threshold}"
                f"{guardrail.unit}, severity={guardrail.severity}"
            )
    return lines


def _format_validation_commands(strategy: Any, request: OptimizationRequest | None) -> list[str]:
    lines: list[str] = []
    if strategy.validation_plan.test_command_ids:
        lines.append(
            "Selected strategy validation command ids: "
            f"{strategy.validation_plan.test_command_ids}"
        )
    if strategy.validation_plan.expected_metric_movements:
        lines.append(
            "Expected metric movements: "
            f"{strategy.validation_plan.expected_metric_movements}"
        )
    if request is None or request.execution is None:
        lines.append("- <no docker-compose evaluation contract available>")
        return lines
    lines.append(f"Compose file: {request.execution.compose_file}")
    for evaluation in request.execution.evaluations:
        command = " ".join(evaluation.command.argv)
        lines.append(
            "- "
            f"{evaluation.evaluation_id}: service={evaluation.command.service}, "
            f"command={command!r}, "
            f"cwd={evaluation.command.working_directory or '<service default>'}, "
            f"metrics={sorted(evaluation.expected_metric_ids)}, "
            f"repetitions={evaluation.repetitions}, warmup={evaluation.warmup_runs}"
        )
    return lines


def _phase_template_risk_tier(template: Any, strategy: Any) -> str:
    factors = " ".join(str(item).lower() for item in template.risk_factors)
    variable = str(template.treatment.variable).lower()
    if "architecture" in factors or "migration" in factors:
        return "architecture"
    if "prompt" in factors or "prompt" in variable:
        return "prompt"
    if "config" in factors or "configuration" in factors or "config" in variable:
        return "experiment_config"
    if template.phase_kind == "diagnostic":
        return "experiment_config"
    return cast("str", strategy.risk_ceiling)


def _phase_template_risk_tiers(strategy: Any) -> dict[str, str]:
    return {
        template.phase_id: _phase_template_risk_tier(template, strategy)
        for template in strategy.phase_templates
    }


def _strategy_validation_command_ids(
    strategy: Any, request: OptimizationRequest | None
) -> list[str]:
    command_ids = list(strategy.validation_plan.test_command_ids)
    if command_ids:
        return command_ids
    if request is not None and request.execution is not None:
        return [evaluation.evaluation_id for evaluation in request.execution.evaluations]
    return []


def _build_plan_context(
    strategy: Any,
    dependency_map: dict[str, list[str]],
    *,
    snapshot: SourceSnapshot,
    request: OptimizationRequest | None,
) -> str:
    allowed_paths = _allowed_plan_paths(strategy, dependency_map, snapshot, request)
    lines = [
        f"Strategy: {strategy.title}",
        f"Mechanism: {strategy.mechanism}",
        f"Risk ceiling: {strategy.risk_ceiling}",
        "\nRequester criteria. Done criteria must mention the real metric id/target, "
        "not only the internal criterion id:",
        *_format_request_criteria(request),
        "\nValidation commands. Every implementation phase must be verifiable by these "
        "repository-owned commands:",
        *_format_validation_commands(strategy, request),
        "\nExisting phase templates (refine/extend, keep sequence order):",
    ]
    for template in strategy.phase_templates:
        risk_tier = _phase_template_risk_tier(template, strategy)
        lines.append(
            f"- {template.phase_id} (seq={template.sequence}, kind={template.phase_kind}, "
            f"risk_tier={risk_tier}): "
            f"{template.treatment.variable} {template.treatment.before} -> "
            f"{template.treatment.after}"
        )
    lines.append("\nCriteria this strategy claims to affect (must be covered by done_criteria):")
    for impact in strategy.impact_assessment.criterion_impacts:
        criterion_hint = ""
        if request is not None:
            for criterion in request.criteria:
                if criterion.criterion_id == impact.criterion_id:
                    criterion_hint = (
                        f", metric={criterion.metric_id}, target={criterion.target}{criterion.unit}"
                    )
                    break
        lines.append(
            f"- {impact.criterion_id} ({impact.direction}, confidence={impact.confidence}"
            f"{criterion_hint})"
        )
    lines.append("\nScope entries and any real test files discovered for them:")
    for entry in strategy.scope_resolution.entries:
        tests = dependency_map.get(entry.path_or_symbol, [])
        lines.append(f"- {entry.path_or_symbol} ({entry.kind}) tests={tests}")
    lines.append(
        "\nAllowed existing repository paths for task.files. Use exact strings from this "
        "list. If a file is not listed, do not include it unless proposed_creation=true "
        "and the task explicitly creates a new file:"
    )
    lines.extend(f"- {path}" for path in allowed_paths)
    lines.append(
        "\nRequired phase metadata: copy risk_tier from the phase template, "
        "set affected_criteria to the criterion ids the phase validates, and set "
        "validation_command_ids to repository-owned validation command ids above. "
        "Order phase sequence by risk ladder: experiment_config < prompt < code < architecture."
    )
    return "\n".join(lines)


def _resolve_existing_task_file(raw_path: str, allowed_paths: set[str]) -> str | None:
    normalized = _normalize_repo_path(raw_path)
    if normalized in allowed_paths:
        return normalized
    parts = [part for part in normalized.split("/") if part]
    max_suffix_parts = min(len(parts), 4)
    for size in range(max_suffix_parts, 0, -1):
        suffix = "/".join(parts[-size:])
        matches = [path for path in allowed_paths if path.endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
    basename = parts[-1] if parts else normalized
    matches = [path for path in allowed_paths if path.rsplit("/", maxsplit=1)[-1] == basename]
    if len(matches) == 1:
        return matches[0]
    return None


def _repair_task_file_paths(
    files: list[str], *, allowed_paths: set[str]
) -> tuple[list[str], list[str]]:
    repaired: list[str] = []
    repair_notes: list[str] = []
    for raw_path in files:
        normalized = _normalize_repo_path(raw_path)
        resolved = _resolve_existing_task_file(normalized, allowed_paths)
        if resolved is None:
            repaired.append(normalized)
            continue
        if resolved != normalized:
            repair_notes.append(f"{raw_path} -> {resolved}")
        repaired.append(resolved)
    return repaired, repair_notes


def _path_resolution_failures(
    tasks: list[PlanTask], *, allowed_paths: set[str]
) -> list[str]:
    return [
        f"{task.task_id}: {file} does not exist and is not marked proposed_creation"
        for task in tasks
        for file in task.files
        if not task.proposed_creation and _normalize_repo_path(file) not in allowed_paths
    ]


def _criterion_coverage(
    phases: list[ExecutionPhase],
    strategy: Any,
    request: OptimizationRequest | None,
) -> dict[str, bool]:
    done_criteria = [criterion.lower() for phase in phases for criterion in phase.done_criteria]
    request_criteria = (
        {criterion.criterion_id: criterion for criterion in request.criteria}
        if request is not None
        else {}
    )

    coverage: dict[str, bool] = {}
    for impact in strategy.impact_assessment.criterion_impacts:
        criterion = request_criteria.get(impact.criterion_id)
        phases_affecting_criterion = [
            phase for phase in phases if impact.criterion_id in phase.affected_criteria
        ]
        if not phases_affecting_criterion:
            coverage[impact.criterion_id] = False
            continue
        if not any(phase.validation_command_ids for phase in phases_affecting_criterion):
            coverage[impact.criterion_id] = False
            continue
        if criterion is None:
            coverage[impact.criterion_id] = any(
                impact.criterion_id.lower() in criterion_text
                for phase in phases_affecting_criterion
                for criterion_text in [item.lower() for item in phase.done_criteria]
            )
            continue
        criterion_id = criterion.criterion_id.lower()
        metric_id = criterion.metric_id.lower()
        target_text = str(criterion.target).lower()
        coverage[impact.criterion_id] = any(
            (
                criterion_id in done
                and metric_id in done
                and (
                    target_text in done
                    or criterion.direction in done
                    or criterion.aggregation in done
                )
            )
            or (metric_id in done and target_text in done)
            for done in done_criteria
        )
    return coverage


def _grounded_critique(
    parsed: dict[str, Any],
    *,
    phases: list[ExecutionPhase],
    tasks: list[PlanTask],
    coverage: dict[str, bool],
    rollback_reasons: list[str],
) -> dict[str, Any]:
    raw_omissions = cast("list[Any]", parsed.get("omissions") or [])
    raw_concerns = cast("list[Any]", parsed.get("concerns") or [])
    omissions = [str(item) for item in raw_omissions if str(item).strip()]
    concerns = [str(item) for item in raw_concerns if str(item).strip()]
    approved = bool(parsed.get("approved"))

    anchors: set[str] = set()
    anchors.update(phase.phase_id.lower() for phase in phases)
    anchors.update(phase.treatment.variable.lower() for phase in phases)
    anchors.update(task.task_id.lower() for task in tasks)
    anchors.update(file.lower() for task in tasks for file in task.files)
    anchors.update(criterion_id.lower() for criterion_id in coverage)
    anchors.update({"criteria", "criterion", "done_criteria", "rollback", "dependency"})
    anchors.update(reason.lower() for reason in rollback_reasons)

    def grounded(message: str) -> bool:
        lower = message.lower()
        return any(anchor and anchor in lower for anchor in anchors)

    grounded_omissions = [message for message in omissions if grounded(message)]
    grounded_concerns = [message for message in concerns if grounded(message)]
    ignored = [
        message
        for message in [*omissions, *concerns]
        if message not in grounded_omissions and message not in grounded_concerns
    ]
    return {
        "omissions": grounded_omissions,
        "concerns": grounded_concerns,
        "ignored_ungrounded": ignored,
        "approved": approved or (not grounded_omissions and not grounded_concerns),
    }


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


def _check_risk_ladder_order(phases: list[ExecutionPhase]) -> tuple[bool, str | None]:
    ordered = sorted(phases, key=lambda phase: phase.sequence)
    tiers = [_RISK_TIER_ORDER[phase.risk_tier] for phase in ordered]
    if tiers == sorted(tiers):
        return True, None
    detail = [
        f"{phase.phase_id}(seq={phase.sequence}, risk_tier={phase.risk_tier})"
        for phase in ordered
    ]
    return False, (
        "phase risk_tier ordering violates risk ladder "
        "experiment_config < prompt < code < architecture: "
        + " -> ".join(detail)
    )


# ---------------------------------------------------------------------------
# Shared helpers (mirrors s01_handlers.py/c0_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "OptimizationRequest": cast("type[ArtifactEnvelope]", OptimizationRequest),
    "SelectedSolution": cast("type[ArtifactEnvelope]", SelectedSolution),
    "SolutionPortfolio": cast("type[ArtifactEnvelope]", SolutionPortfolio),
    "SourceSnapshot": cast("type[ArtifactEnvelope]", SourceSnapshot),
    "RepositoryManifest": cast("type[ArtifactEnvelope]", RepositoryManifest),
}


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> Any:
    ref = _require_ref(state, artifact_type)
    return _read_model(ports, state, ref, _MODEL_BY_TYPE[artifact_type])


def _read_optional(ports: NodePorts, state: OptimizationState, artifact_type: str) -> Any | None:
    ref = _try_ref(state, artifact_type)
    if ref is None:
        return None
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
