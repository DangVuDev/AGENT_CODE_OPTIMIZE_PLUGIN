# pyright: reportPrivateUsage=false
"""Implementation of business node S02.30."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _PLAN_DRAFT_SCHEMA,
    _PROMPT_VERSION,
    _build_plan_context,
    _model_id,
    _one_repair_complete,
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
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    dependency_map = cast("dict[str, list[str]]", state.get("s02_dependency_map") or {})
    context = _build_plan_context(strategy, dependency_map)

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
        "real rollback_trigger/rollback_deadline_seconds. Never invent files "
        "or symbols not present in the scope entries."
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
