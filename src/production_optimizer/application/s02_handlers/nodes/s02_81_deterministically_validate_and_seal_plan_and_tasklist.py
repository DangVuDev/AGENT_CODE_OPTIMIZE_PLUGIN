# pyright: reportPrivateUsage=false
"""Implementation of business node S02.81."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.s02 import (
    ExecutionPhase,
    ExecutionPlan,
    PlanQualityReport,
    PlanQualityResult,
    PlanTask,
    TaskList,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _MAX_S02_REVISIONS,
    _base_envelope,
    _check_acyclic,
    _check_phase_risk_order,
    _check_risk_ladder_order,
    _pass_stage_id,
    _path_resolution_failures,
    _put_envelope,
    _read_required,
    _repository_file_paths,
    _seal,
    _selected_solution_ref,
    _stage_envelope,
)


def handle_s02_81_deterministically_validate_and_seal_plan_and_tasklist(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """The only node that seals `ExecutionPlan`/`TaskList`/
    `PlanQualityReport`, and the sole entry point for a bounded redraft
    (mirrors A3's `_MAX_REVISION_ATTEMPTS`)."""

    selected_ref = _selected_solution_ref(state)
    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    phases = cast("list[ExecutionPhase]", draft.get("phases") or [])
    tasks = cast("list[PlanTask]", draft.get("tasks") or [])
    allowed_paths = set(_repository_file_paths(snapshot))

    phase_failures = cast("list[str]", draft.get("phase_failures") or [])
    task_failures = cast("list[str]", draft.get("task_failures") or [])
    dag_ok, dag_detail = _check_acyclic(tasks)
    risk_order_ok, risk_order_detail = _check_phase_risk_order(phases)
    risk_ladder_ok, risk_ladder_detail = _check_risk_ladder_order(phases)
    path_failures = _path_resolution_failures(tasks, allowed_paths=allowed_paths)
    path_repairs = cast("list[str]", draft.get("path_repairs") or [])
    coverage = cast("dict[str, bool]", draft.get("acceptance_coverage") or {})
    rollback_ok = bool(draft.get("rollback_ok"))
    rollback_reasons = cast("list[str]", draft.get("rollback_reasons") or [])
    critique = cast("dict[str, Any]", state.get("s02_critique") or {})
    critic_approved = bool(critique.get("approved"))

    results = [
        PlanQualityResult(
            dimension="phases_well_formed",
            passed=bool(phases) and not phase_failures,
            detail="; ".join(phase_failures) or None,
        ),
        PlanQualityResult(
            dimension="tasks_well_formed",
            passed=bool(tasks) and not task_failures,
            detail="; ".join(task_failures) or None,
        ),
        PlanQualityResult(dimension="dependency_dag_acyclic", passed=dag_ok, detail=dag_detail),
        PlanQualityResult(
            dimension="phase_ordering_by_risk",
            passed=risk_order_ok,
            detail=risk_order_detail,
        ),
        PlanQualityResult(
            dimension="risk_ladder_ordering",
            passed=risk_ladder_ok,
            detail=risk_ladder_detail,
        ),
        PlanQualityResult(
            dimension="paths_resolve",
            passed=not path_failures,
            detail="; ".join(path_failures) or None,
        ),
        # S02.50's `_repair_task_file_paths` silently rewrites a
        # model-hallucinated path to the one real repository file whose
        # basename/suffix uniquely matches it -- useful for cosmetic
        # normalization (`\` vs `/`, a leading `./`), but a real repair
        # means the model asserted a path that did not exist. Surfacing it
        # here (rather than letting `paths_resolve` silently see only the
        # already-rewritten, now-existing path) makes that fact a real,
        # blocking quality dimension instead of a silent bypass: it drives
        # the same bounded-revision loop as any other failing dimension.
        PlanQualityResult(
            dimension="no_silent_path_repairs",
            passed=not path_repairs,
            detail="; ".join(path_repairs) or None,
        ),
        PlanQualityResult(
            dimension="criteria_coverage",
            passed=bool(coverage) and all(coverage.values()),
            detail=(
                None
                if (coverage and all(coverage.values()))
                else f"uncovered: {sorted(k for k, v in coverage.items() if not v)}"
            ),
        ),
        PlanQualityResult(
            dimension="rollback_defined",
            passed=rollback_ok,
            detail="; ".join(rollback_reasons) or None,
        ),
        PlanQualityResult(
            dimension="critic_approved",
            passed=critic_approved,
            detail="; ".join(cast("list[str]", critique.get("concerns") or [])) or None,
        ),
    ]
    passed = all(result.passed for result in results)

    refs: list[ArtifactRef] = []
    execution_plan_digest: str | None = None
    task_list_digest: str | None = None
    if passed:
        execution_plan = _seal(
            ExecutionPlan(
                **_base_envelope(state, "ExecutionPlan"),
                selected_solution_digest=selected_ref.content_digest,
                phases=phases,
            )
        )
        execution_plan_ref = _put_envelope(ports, state, execution_plan, node_id="S02.81")
        task_list = _seal(
            TaskList(
                **_base_envelope(state, "TaskList"),
                execution_plan_digest=execution_plan_ref.content_digest,
                tasks=tasks,
            )
        )
        task_list_ref = _put_envelope(ports, state, task_list, node_id="S02.81")
        refs.extend([execution_plan_ref, task_list_ref])
        execution_plan_digest = execution_plan_ref.content_digest
        task_list_digest = task_list_ref.content_digest

    # `PlanQualityReport` alone needs a pass-scoped `artifact_id`: S02.81 can
    # legitimately fire more than once per case (the bounded redraft loop
    # back to S02.30), and each attempt gets a fresh `created_at` -- the
    # default `_base_envelope` artifact_id would collide across attempts the
    # moment content genuinely differs, which `merge_artifact_refs`
    # (contracts/state.py) correctly treats as a hard conflict during a real
    # `graph.invoke()`. Mirrors `a3_handlers._stage_envelope`/`_pass_stage_id`
    # exactly. `ExecutionPlan`/`TaskList` need no such scoping: they are only
    # ever sealed on the one passing attempt that ends the loop.
    pass_number = state.get("s02_revision_attempts", 0) or 0
    quality_report = _seal(
        PlanQualityReport(
            **_stage_envelope(state, _pass_stage_id("S02.81", pass_number), "PlanQualityReport"),
            execution_plan_digest=execution_plan_digest,
            task_list_digest=task_list_digest,
            passed=passed,
            results=results,
        )
    )
    refs.append(_put_envelope(ports, state, quality_report, node_id="S02.81"))

    if passed:
        route = NodeRoute.CONTINUE
    else:
        route = NodeRoute.REVISION if pass_number < _MAX_S02_REVISIONS else NodeRoute.REJECTED

    # Unconditional +1 on *every* invocation (not just on an actual
    # "revision" route): `NodeRuntime._commit`/`route_for`
    # (application/node_runtime.py) key this node's recorded route by the
    # post-update value of this same counter, so each of S02.81's
    # invocations within one bounded-redraft run needs its own distinct
    # value even on the terminal (REJECTED) call, which does not loop back.
    # Conditioning this on the route (as A3's `a3_revision_attempts` does)
    # would give the terminal call the *same* post-update value as the
    # revision call immediately before it, colliding under one route key.
    updates: dict[str, Any] = {"artifact_refs": refs, "s02_revision_attempts": 1}
    return NodeExecution(route=route, updates=updates)


__all__ = ["handle_s02_81_deterministically_validate_and_seal_plan_and_tasklist"]
