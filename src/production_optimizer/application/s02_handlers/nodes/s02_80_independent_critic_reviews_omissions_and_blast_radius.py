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
    _grounded_critique,
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
    coverage = cast("dict[str, bool]", draft.get("acceptance_coverage") or {})
    rollback_reasons = cast("list[str]", draft.get("rollback_reasons") or [])

    context_lines = ["Phases:"]
    context_lines.extend(
        f"- {phase.phase_id}: kind={phase.phase_kind} risk_tier={phase.risk_tier} "
        f"treatment={phase.treatment.variable} affected_criteria={phase.affected_criteria} "
        f"validation_command_ids={phase.validation_command_ids} "
        f"done_criteria={phase.done_criteria} rollback={phase.rollback_command}"
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
        "real gaps. A blocking omission/concern must cite concrete plan "
        "anchors: a phase_id, task_id, exact file path, criterion id, done "
        "criterion, dependency, or rollback issue present in the supplied "
        "plan. Do not speculate about database, concurrency, caching, or "
        "architecture risk unless the plan text or file paths directly show "
        "that risk. Set approved=false only for grounded blocking issues. "
        "Apply this platform's own rollback rule exactly, and do not invent "
        "a stricter one: ONLY a phase with kind=implementation is required "
        "to carry a rollback command. A phase with kind=diagnostic is "
        "read-only/reversible investigation by definition and is PERMITTED "
        "to have rollback=None -- never raise that as an omission or "
        "concern, even when the diagnostic phase touches a file. A separate "
        "deterministic check already enforces the implementation-phase rule, "
        "so flagging a missing rollback on a diagnostic phase is a false "
        "positive that blocks a valid plan."
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
        _grounded_critique(
            parsed,
            phases=phases,
            tasks=tasks,
            coverage=coverage,
            rollback_reasons=rollback_reasons,
        )
        if parsed is not None
        else {
            "omissions": [],
            "concerns": ["critic did not produce schema-valid output"],
            "ignored_ungrounded": [],
            "approved": False,
        }
    )
    return NodeExecution(updates={"s02_critique": critique})


__all__ = ["handle_s02_80_independent_critic_reviews_omissions_and_blast_radius"]
