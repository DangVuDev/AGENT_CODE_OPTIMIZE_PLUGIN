# pyright: reportPrivateUsage=false
"""Implementation of business node S02.80."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole
from production_optimizer.contracts.s02 import ExecutionPhase, PlanTask
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _CRITIQUE_SCHEMA,
    _PROMPT_VERSION,
    _model_id,
    _one_repair_complete,
    _required_state_str,
)


def handle_s02_80_independent_critic_reviews_omissions_and_blast_radius(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("S02.80 requires ModelProviderPort wired into NodePorts")
    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    phases = cast("list[ExecutionPhase]", draft.get("phases") or [])
    tasks = cast("list[PlanTask]", draft.get("tasks") or [])

    context_lines = ["Phases:"]
    context_lines.extend(
        f"- {phase.phase_id}: {phase.treatment.variable} done_criteria={phase.done_criteria}"
        for phase in phases
    )
    context_lines.append("Tasks:")
    context_lines.extend(
        f"- {task.task_id} (phase={task.phase_id}): {task.objective} files={task.files}"
        for task in tasks
    )
    context = "\n".join(context_lines)

    system_prompt = (
        "You are an independent critic reviewing an implementation plan for "
        "omissions, unstated assumptions and blast-radius concerns. You did "
        "not draft this plan and must not approve it uncritically -- flag "
        "real gaps. Set approved=false if any phase lacks a clear done "
        "criterion or the blast radius looks understated."
    )
    request = ModelCompletionRequest(
        role=ModelRole.JUDGE,
        model_id=_model_id(ports),
        prompt_version=_PROMPT_VERSION,
        messages=[
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(role="user", content=context),
        ],
        response_schema=_CRITIQUE_SCHEMA,
        max_output_tokens=1500,
        idempotency_key=f"{_required_state_str(state, 'case_id')}:S02.80:{_PROMPT_VERSION}",
    )
    parsed, _tokens = _one_repair_complete(ports, request)
    critique = (
        parsed
        if parsed is not None
        else {
            "omissions": [],
            "concerns": ["critic did not produce schema-valid output"],
            "approved": False,
        }
    )
    return NodeExecution(updates={"s02_critique": critique})


__all__ = ["handle_s02_80_independent_critic_reviews_omissions_and_blast_radius"]
