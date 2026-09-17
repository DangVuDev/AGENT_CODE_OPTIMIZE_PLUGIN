# pyright: reportPrivateUsage=false
"""Implementation of business node S02.40."""

from __future__ import annotations

from typing import Any, cast

from pydantic import ValidationError

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.s02 import ExecutionPhase
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _RISK_TIER_ORDER,
    _phase_template_risk_tiers,
    _read_optional,
    _read_required,
    _read_selected_solution,
    _strategy_by_id,
    _strategy_validation_command_ids,
)


def handle_s02_40_materialize_and_validate_draft_phases(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Materialize phases: real Pydantic validation of the draft, not a
    feasibility check (feasibility is S02.81's job)."""

    selected = _read_selected_solution(ports, state)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    request = cast(
        "OptimizationRequest | None", _read_optional(ports, state, "OptimizationRequest")
    )
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    risk_by_phase_id = _phase_template_risk_tiers(strategy)
    affected_criteria = [
        impact.criterion_id for impact in strategy.impact_assessment.criterion_impacts
    ]
    validation_command_ids = _strategy_validation_command_ids(strategy, request)
    draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
    phases: list[ExecutionPhase] = []
    failures: list[str] = []
    metadata_repairs: list[str] = []
    for raw in draft.get("phases", []):
        raw = cast("dict[str, Any]", raw)
        phase_id = str(raw.get("phase_id") or "<unknown>")
        expected_risk_tier = risk_by_phase_id.get(phase_id, strategy.risk_ceiling)
        updates: dict[str, Any] = {}
        declared_risk_tier = raw.get("risk_tier")
        if declared_risk_tier not in _RISK_TIER_ORDER:
            # Only fill in when the model's own declaration is missing or
            # not one of the four valid tiers -- a validly-declared tier
            # that merely differs from this heuristic's own guess is never
            # overridden: the model may legitimately know more about a
            # phase's real risk (e.g. it touches a service boundary) than a
            # crude keyword-substring heuristic can infer.
            updates["risk_tier"] = expected_risk_tier
            metadata_repairs.append(
                f"{phase_id}: risk_tier missing/invalid ({declared_risk_tier!r}) -> "
                f"{expected_risk_tier!r}"
            )
        if not raw.get("affected_criteria"):
            updates["affected_criteria"] = affected_criteria
            metadata_repairs.append(f"{phase_id}: filled affected_criteria={affected_criteria}")
        if not raw.get("validation_command_ids"):
            updates["validation_command_ids"] = validation_command_ids
            metadata_repairs.append(
                f"{phase_id}: filled validation_command_ids={validation_command_ids}"
            )
        if updates:
            raw = {**raw, **updates}
        try:
            phases.append(ExecutionPhase.model_validate(raw))
        except ValidationError as exc:
            failures.append(f"{raw.get('phase_id', '<unknown>')}: {exc}")
    updated = {
        **draft,
        "phases": phases,
        "phase_failures": failures,
        "phase_metadata_repairs": metadata_repairs,
    }
    return NodeExecution(updates={"s02_plan_draft": updated})


__all__ = ["handle_s02_40_materialize_and_validate_draft_phases"]
