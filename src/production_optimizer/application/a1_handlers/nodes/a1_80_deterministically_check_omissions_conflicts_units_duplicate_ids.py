# pyright: reportPrivateUsage=false
"""Implementation of business node A1.80."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import (
    A1QualityReport,
    CanonicalObjective,
    CriterionSet,
    EvidenceRequirementSet,
    ExecutionBudgetArtifact,
    FeatureScope,
    GuardrailSet,
    ManualCasePayload,
    ProjectProfile,
    RawRequestDraft,
    WorkloadIdentity,
)
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _logger,
    _metric_profile,
    _now,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
)


def _validate_compose_contract(
    payload: ManualCasePayload,
    workload: WorkloadIdentity,
    criteria: CriterionSet,
    guardrails: GuardrailSet,
) -> list[str]:
    if payload.execution_profile != "docker_compose":
        return []
    if workload.execution is None:
        return ["docker_compose execution contract is required"]

    conflicts: list[str] = []
    repository_root = Path(payload.local_path).resolve()
    compose_path = (repository_root / workload.execution.compose_file).resolve()
    if not compose_path.is_relative_to(repository_root) or not compose_path.is_file():
        conflicts.append("compose_file must be an existing file inside the repository")
    emitted_metrics = {
        metric_id
        for evaluation in workload.execution.evaluations
        for metric_id in evaluation.expected_metric_ids
    }
    required_metrics = {criterion.metric_id for criterion in criteria.criteria} | {
        guardrail.metric_id for guardrail in guardrails.guardrails
    }
    conflicts.extend(
        f"metric {metric_id!r} is not bound to any declared evaluation"
        for metric_id in sorted(required_metrics - emitted_metrics)
    )
    return conflicts


def _validate_evidence_bindings(
    criteria: CriterionSet, evidence: EvidenceRequirementSet
) -> list[str]:
    conflicts: list[str] = []
    requirement_by_criterion = {
        requirement.criterion_id: requirement for requirement in evidence.evidence_requirements
    }
    for criterion in criteria.criteria:
        requirement = requirement_by_criterion.get(criterion.criterion_id)
        if requirement is None:
            conflicts.append(f"criterion {criterion.criterion_id!r} has no evidence requirement")
            continue
        expected = _metric_profile(
            criterion.metric_id, unit=criterion.unit, direction=criterion.direction
        )
        if not requirement.accepted_source_types <= cast("set[str]", expected["source_types"]):
            conflicts.append(
                f"criterion {criterion.criterion_id!r} accepts evidence incompatible with "
                f"metric {criterion.metric_id!r}"
            )
        if requirement.canonical_unit != criterion.unit:
            conflicts.append(f"criterion {criterion.criterion_id!r} evidence unit is inconsistent")
    return conflicts


def _validate_budget(
    workload: WorkloadIdentity, budget: ExecutionBudgetArtifact
) -> tuple[list[str], list[str]]:
    invalid_values: list[str] = []
    if workload.workload is not None:
        minimum_worker_seconds = workload.workload.repetitions + workload.workload.warmup_runs
        if budget.budget.maximum_worker_seconds < minimum_worker_seconds:
            invalid_values.append(
                "maximum_worker_seconds cannot execute the declared repetitions and warmups"
            )
        if workload.workload.concurrency > budget.budget.maximum_concurrency:
            invalid_values.append("workload concurrency exceeds execution budget")
    warnings = (
        ["worker budget exceeds the wall-clock deadline and may be cut short"]
        if budget.budget.maximum_worker_seconds > budget.budget.deadline_seconds
        else []
    )
    return invalid_values, warnings


def handle_a1_80_deterministically_check_omissions_conflicts_units_duplicate_ids(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    objective = _read_model(
        ports, state, _require_ref(state, "CanonicalObjective"), CanonicalObjective
    )
    criteria = _read_model(ports, state, _require_ref(state, "CriterionSet"), CriterionSet)
    guardrails = _read_model(ports, state, _require_ref(state, "GuardrailSet"), GuardrailSet)
    workload = _read_model(ports, state, _require_ref(state, "WorkloadIdentity"), WorkloadIdentity)
    evidence = _read_model(
        ports, state, _require_ref(state, "EvidenceRequirementSet"), EvidenceRequirementSet
    )
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    profile = _read_model(ports, state, _require_ref(state, "ProjectProfile"), ProjectProfile)
    scope = _read_model(ports, state, _require_ref(state, "FeatureScope"), FeatureScope)
    budget = _read_model(
        ports,
        state,
        _require_ref(state, "ExecutionBudgetArtifact"),
        ExecutionBudgetArtifact,
    )
    case_id = _required_state_str(state, "case_id")
    missing = sorted(
        set(
            objective.missing_fields
            + criteria.missing_fields
            + guardrails.missing_fields
            + workload.missing_fields
            + evidence.missing_fields
        )
    )
    missing = sorted(set([*missing, *draft.unresolved_fields]))
    conflicts: list[str] = list(draft.conflicts)
    if scope.confidence < 0.7:
        conflicts.append(f"feature scope confidence {scope.confidence:.2f} is below 0.70")
    if objective.objective is None:
        conflicts.append("objective is not canonicalized")
    if not criteria.criteria:
        conflicts.append("at least one primary criterion is required")
    if not guardrails.guardrails:
        conflicts.append("at least one correctness guardrail is required")
    if workload.workload is None:
        conflicts.append("workload identity is required")
    conflicts.extend(_validate_compose_contract(payload, workload, criteria, guardrails))
    if not evidence.evidence_requirements:
        conflicts.append("evidence requirements are required")
    conflicts.extend(_validate_evidence_bindings(criteria, evidence))
    invalid_values, warnings = _validate_budget(workload, budget)
    if profile.discovery_truncated:
        conflicts.append(
            f"project discovery was truncated at {profile.file_count} files; "
            "analysis may be incomplete for very large repositories"
        )
        _logger.info(
            "A1.80: project discovery truncated",
            extra={"case_id": case_id, "file_count": profile.file_count},
        )
    passed = not missing and not conflicts and not invalid_values
    report = _seal(
        A1QualityReport(
            **_base_envelope(
                state,
                "A1QualityReport",
                parents=[
                    objective.content_digest,
                    criteria.content_digest,
                    guardrails.content_digest,
                    workload.content_digest,
                    evidence.content_digest,
                    draft.content_digest,
                    profile.content_digest,
                    scope.content_digest,
                    budget.content_digest,
                ],
            ),
            passed=passed,
            missing_fields=missing,
            conflicts=conflicts,
            invalid_values=invalid_values,
            warnings=warnings,
            repair_owners={
                **{field: "requester" for field in missing},
                **{conflict: "requester" for conflict in conflicts},
                **{invalid: "requester" for invalid in invalid_values},
            },
            policy_version=payload.policy_version,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="A1.80")
    if passed:
        return NodeExecution(updates={"artifact_refs": [ref]})

    interrupt = InterruptEnvelope(
        interrupt_id=f"{case_id}-A1-CLARIFICATION",
        case_id=case_id,
        thread_id=_required_state_str(state, "thread_id"),
        stage="A1.80",
        artifact_digest=report.content_digest,
        allowed_decisions=["revise", "reject"],
        required_actor_role="requester",
        policy_version=payload.policy_version,
        issued_at=_now(),
        expires_at=_now() + timedelta(hours=24),
    )
    return NodeExecution(
        route=NodeRoute.CLARIFICATION,
        updates={"artifact_refs": [ref], "pending_interrupt": interrupt},
    )


__all__ = ["handle_a1_80_deterministically_check_omissions_conflicts_units_duplicate_ids"]
