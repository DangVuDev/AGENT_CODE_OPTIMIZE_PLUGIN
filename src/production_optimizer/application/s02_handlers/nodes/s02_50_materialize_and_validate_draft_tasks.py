# pyright: reportPrivateUsage=false
"""Implementation of business node S02.50."""

from __future__ import annotations

from typing import Any, cast

from pydantic import ValidationError

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.s02 import ExecutionPhase, PlanTask
from production_optimizer.contracts.state import OptimizationState

from ..shared import _read_required, _repair_task_file_paths, _repository_file_paths


def handle_s02_50_materialize_and_validate_draft_tasks(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Generate tasks: real validation plus a real cross-check that every
    task references a phase S02.40 actually kept."""

    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    allowed_paths = set(_repository_file_paths(snapshot))
    phases = cast("list[ExecutionPhase]", draft.get("phases") or [])
    known_phase_ids = {phase.phase_id for phase in phases}
    tasks: list[PlanTask] = []
    failures: list[str] = []
    path_repairs: list[str] = []
    for raw in draft.get("tasks", []):
        raw_files = cast("list[str]", raw.get("files") or [])
        repaired_files, repair_notes = _repair_task_file_paths(
            raw_files, allowed_paths=allowed_paths
        )
        raw = {**raw, "files": repaired_files}
        path_repairs.extend(f"{raw.get('task_id', '<unknown>')}: {note}" for note in repair_notes)
        try:
            task = PlanTask.model_validate(raw)
        except ValidationError as exc:
            failures.append(f"{raw.get('task_id', '<unknown>')}: {exc}")
            continue
        if task.phase_id not in known_phase_ids:
            failures.append(f"{task.task_id}: references unknown phase {task.phase_id!r}")
            continue
        tasks.append(task)
    updated = {**draft, "tasks": tasks, "task_failures": failures, "path_repairs": path_repairs}
    return NodeExecution(updates={"s02_plan_draft": updated})


__all__ = ["handle_s02_50_materialize_and_validate_draft_tasks"]
