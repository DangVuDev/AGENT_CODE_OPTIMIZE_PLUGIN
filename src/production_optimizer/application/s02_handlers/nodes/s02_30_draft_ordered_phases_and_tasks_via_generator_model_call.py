# pyright: reportPrivateUsage=false
"""Implementation of business node S02.30."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _PLAN_DRAFT_SCHEMA,
    _PROMPT_VERSION,
    _build_plan_context,
    _model_id,
    _one_repair_complete,
    _read_optional,
    _read_required,
    _read_selected_solution,
    _required_state_str,
    _strategy_by_id,
)


def handle_s02_30_draft_ordered_phases_and_tasks_via_generator_model_call(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("S02.30 requires ModelProviderPort wired into NodePorts")
    selected = _read_selected_solution(ports, state)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    request = cast(
        "OptimizationRequest | None", _read_optional(ports, state, "OptimizationRequest")
    )
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    dependency_map = cast("dict[str, list[str]]", state.get("s02_dependency_map") or {})
    context = _build_plan_context(
        strategy,
        dependency_map,
        snapshot=snapshot,
        request=request,
    )

    system_prompt = (
        "You are the S02 implementation planner for an evidence-grounded code "
        "optimization platform. Turn the given strategy into ordered phases and "
        "tasks. Each phase changes exactly one logical treatment. Order phases "
        "cheap-to-expensive: every 'diagnostic' phase (read-only, reversible "
        "investigation) must sequence strictly before every 'implementation' "
        "phase (a real code change) -- never interleave or reverse this, an "
        "implementation phase must not run to confirm a hypothesis a cheaper "
        "diagnostic phase could have tested first. Every phase must state "
        "done_criteria that reference the criteria it addresses by id, and a "
        "real rollback_trigger/rollback_deadline_seconds. "
        "MANDATORY: every phase with phase_kind='implementation' MUST set a "
        "non-null rollback_command -- a real, literal shell command (e.g. "
        "'git checkout -- <path>' or a specific revert command) that undoes "
        "this phase's change if rollback_trigger fires. A plan with any "
        "implementation phase missing rollback_command will be rejected "
        "outright; do not omit it and do not describe rollback only in prose "
        "-- it must be the actual rollback_command field. "
        "Task files must use EXACT strings from 'Allowed existing repository "
        "paths' below -- copy them character-for-character, never guess, "
        "abbreviate, or infer a plausible-looking path. Only use a path "
        "outside that list if the task sets proposed_creation=true and is "
        "genuinely creating a new file; never invent a path for a file the "
        "task only modifies. "
        "Each phase must include risk_tier, affected_criteria, and "
        "validation_command_ids. risk_tier must be copied from the matching "
        "phase template. Phase sequence must follow the risk ladder "
        "experiment_config < prompt < code < architecture. "
        "Done criteria must include concrete metric ids, directions, and target "
        "thresholds from the requester criteria, not only internal labels like "
        "'primary'. Do not propose database, concurrency, caching, or service "
        "architecture work unless the provided strategy/scope/source paths "
        "directly mention that concern. If a task DOES introduce any caching "
        "or in-memory storage layer, its instructions must explicitly state "
        "the cache invalidation strategy (what triggers eviction/refresh) and "
        "any memory/size bound -- a caching task with no invalidation "
        "strategy or memory limit stated will be rejected."
    )
    request = ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id=_model_id(ports),
        prompt_version=_PROMPT_VERSION,
        messages=[
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(role="user", content=context),
        ],
        response_schema=_PLAN_DRAFT_SCHEMA,
        max_output_tokens=6000,
        idempotency_key=f"{_required_state_str(state, 'case_id')}:S02.30:{_PROMPT_VERSION}",
    )
    parsed, _tokens = _one_repair_complete(ports, request)
    draft: dict[str, Any] = parsed if parsed is not None else {"phases": [], "tasks": []}
    return NodeExecution(updates={"s02_plan_draft": draft})


__all__ = ["handle_s02_30_draft_ordered_phases_and_tasks_via_generator_model_call"]
