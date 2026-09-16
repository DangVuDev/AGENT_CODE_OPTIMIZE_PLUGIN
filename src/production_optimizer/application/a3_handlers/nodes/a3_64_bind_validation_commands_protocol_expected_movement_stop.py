# pyright: reportPrivateUsage=false
"""Implementation of business node A3.64."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import VerificationManifest
from production_optimizer.contracts.a3 import (
    RollbackPlan,
    StrategyDraft,
    StrategyDraftSet,
    ValidationPlan,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _expected_metric_movements,
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
    _validation_benchmark_protocol,
    _validation_command_candidates,
)


def handle_a3_64_bind_validation_commands_protocol_expected_movement_stop(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Binds validation to real A2.31 commands; rollback via `git revert`.

    Never invents a validation command (per playbook) — a strategy gets no
    `test_command_ids` unless `VerificationManifest` (A2.31) actually has
    repository-owned commands. `git revert <commit-sha>` is always real and
    executable for any single-commit change regardless of which files it
    touches; precise per-file scope isn't known until A3.70.
    """

    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.63", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)
    verification = _read_model(
        ports, state, _require_ref(state, "VerificationManifest"), VerificationManifest
    )
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    validation_commands = _validation_command_candidates(request, verification)
    real_command_ids = [command.command_id for command in validation_commands]
    expected_metric_movements = _expected_metric_movements(request)

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        validation_plan = draft.validation_plan
        if validation_plan is None or not validation_plan.test_command_ids:
            if real_command_ids:
                validation_plan = ValidationPlan(
                    plan_id=f"validation-{draft.strategy_id}",
                    strategy_id=draft.strategy_id,
                    test_command_ids=real_command_ids,
                    benchmark_protocol=_validation_benchmark_protocol(validation_commands),
                    expected_metric_movements=expected_metric_movements,
                    stop_conditions=[
                        "any validation command exits nonzero",
                        "any guardrail metric violates its declared threshold",
                    ],
                )
            else:
                reasons.append(
                    "no repository-owned verification command exists to bind a validation plan to"
                )
                validation_plan = ValidationPlan(
                    plan_id=f"validation-{draft.strategy_id}",
                    strategy_id=draft.strategy_id,
                    test_command_ids=[],
                    benchmark_protocol="none available",
                    stop_conditions=["no verification command available"],
                )
        else:
            known_command_ids = set(real_command_ids)
            unknown_command_ids = [
                command_id
                for command_id in validation_plan.test_command_ids
                if command_id not in known_command_ids
            ]
            if unknown_command_ids:
                reasons.append(
                    "validation plan references unknown command ids: "
                    + ", ".join(sorted(unknown_command_ids))
                )
            missing_metric_ids = [
                metric_id
                for metric_id in expected_metric_movements
                if metric_id not in validation_plan.expected_metric_movements
            ]
            if missing_metric_ids:
                validation_plan = validation_plan.model_copy(
                    update={
                        "expected_metric_movements": {
                            **validation_plan.expected_metric_movements,
                            **{
                                metric_id: expected_metric_movements[metric_id]
                                for metric_id in missing_metric_ids
                            },
                        }
                    }
                )

        rollback_plan = draft.rollback_plan
        if rollback_plan is None:
            rollback_plan = RollbackPlan(
                plan_id=f"rollback-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                mechanism="git revert <commit-sha-of-this-change>",
                verification=(
                    "rerun validation_plan.test_command_ids and confirm baseline exit codes "
                    "are unchanged"
                ),
                reversible=True,
            )

        eligible = draft.eligible and not reasons
        updated.append(
            draft.model_copy(
                update={
                    "validation_plan": validation_plan,
                    "rollback_plan": rollback_plan,
                    "gate_reasons": reasons,
                    "eligible": eligible,
                }
            )
        )

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.64", current_pass),
                "StrategyDraftSet",
                parents=[
                    draft_set.content_digest,
                    verification.content_digest,
                    request.content_digest,
                ],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.64")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_64_bind_validation_commands_protocol_expected_movement_stop"]
