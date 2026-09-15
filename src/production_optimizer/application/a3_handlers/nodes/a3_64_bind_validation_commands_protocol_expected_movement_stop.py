# pyright: reportPrivateUsage=false
"""Implementation of business node A3.64."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import VerificationManifest
from production_optimizer.contracts.a3 import (
    RollbackPlan,
    StrategyDraft,
    StrategyDraftSet,
    ValidationPlan,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
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
    real_command_ids = [command.command_id for command in verification.commands]

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        validation_plan = draft.validation_plan
        if validation_plan is None:
            if real_command_ids:
                validation_plan = ValidationPlan(
                    plan_id=f"validation-{draft.strategy_id}",
                    strategy_id=draft.strategy_id,
                    test_command_ids=real_command_ids,
                    benchmark_protocol="rerun repository-owned commands via A2.50 worker jobs",
                    expected_metric_movements={
                        cid: "unchanged-or-improved" for cid in real_command_ids
                    },
                    stop_conditions=["any previously-passing command starts failing"],
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
                parents=[draft_set.content_digest, verification.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.64")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_64_bind_validation_commands_protocol_expected_movement_stop"]
