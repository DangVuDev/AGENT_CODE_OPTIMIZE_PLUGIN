# pyright: reportPrivateUsage=false
"""Implementation of business node A1.90."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import (
    A1ApprovalDecision,
    A1QualityReport,
    ApprovalBinding,
    CanonicalObjective,
    CriterionSet,
    EvidenceRequirementSet,
    ExecutionBudgetArtifact,
    FeatureScope,
    GuardrailSet,
    LocalSourceIdentity,
    ManualCasePayload,
    WorkloadIdentity,
)
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import PolicyRequest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _now,
    _put_envelope,
    _read_model,
    _request_fingerprint,
    _require_ref,
    _required_state_str,
    _seal,
)


def _route_decision(
    state: OptimizationState,
    artifact_ref: object,
    artifact_digest: str,
    route: str,
    policy_version: str,
) -> NodeExecution:
    updates: dict[str, Any] = {"artifact_refs": [artifact_ref]}
    if route == "continue":
        return NodeExecution(updates=updates)
    if route != "approval":
        return NodeExecution(route=NodeRoute.REJECTED, updates=updates)
    interrupt = InterruptEnvelope(
        interrupt_id=f"{_required_state_str(state, 'case_id')}-A1-APPROVAL",
        case_id=_required_state_str(state, "case_id"),
        thread_id=_required_state_str(state, "thread_id"),
        stage="A1.90",
        artifact_digest=artifact_digest,
        allowed_decisions=["approve", "reject"],
        required_actor_role="owner",
        policy_version=policy_version,
        issued_at=_now(),
        expires_at=_now() + timedelta(hours=24),
    )
    updates["pending_interrupt"] = interrupt
    return NodeExecution(route=NodeRoute.APPROVAL, updates=updates)


def handle_a1_90_evaluate_scope_data_risk_rbac_policy_create(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    quality = _read_model(ports, state, _require_ref(state, "A1QualityReport"), A1QualityReport)
    objective = _read_model(
        ports, state, _require_ref(state, "CanonicalObjective"), CanonicalObjective
    )
    criteria = _read_model(ports, state, _require_ref(state, "CriterionSet"), CriterionSet)
    guardrails = _read_model(ports, state, _require_ref(state, "GuardrailSet"), GuardrailSet)
    workload = _read_model(ports, state, _require_ref(state, "WorkloadIdentity"), WorkloadIdentity)
    evidence = _read_model(
        ports, state, _require_ref(state, "EvidenceRequirementSet"), EvidenceRequirementSet
    )
    budget = _read_model(
        ports,
        state,
        _require_ref(state, "ExecutionBudgetArtifact"),
        ExecutionBudgetArtifact,
    )
    source = _read_model(
        ports, state, _require_ref(state, "LocalSourceIdentity"), LocalSourceIdentity
    )
    scope = _read_model(ports, state, _require_ref(state, "FeatureScope"), FeatureScope)
    fingerprint = _request_fingerprint(
        state,
        source=source,
        scope=scope,
        objective=objective,
        criteria=criteria,
        guardrails=guardrails,
        workload=workload,
        evidence=evidence,
        budget=budget,
    )
    approval: ApprovalBinding | None = None
    route = "continue"
    reasons: list[str] = []
    approval_policy_version = payload.policy_version
    resume_command = state.get("resume_command")
    if resume_command is not None:
        approval_policy_version = resume_command.policy_version
        # A prior run already halted this exact case at A1.90 with
        # `NodeRoute.APPROVAL` (see the `elif payload.actor_role ...` branch
        # below) -- `application.resume.resume_case` already verified this
        # command against that halt's `InterruptEnvelope` before re-invoking
        # the graph, so the out-of-band decision is authoritative here and
        # skips re-deriving one from `payload.actor_role`.
        if resume_command.decision == "approve":
            approval = ApprovalBinding(
                approval_id=f"{_required_state_str(state, 'case_id')}-A1-APPROVAL",
                actor_id=resume_command.actor_id,
                actor_role=sorted(resume_command.actor_roles)[0],
                decision="approve",
                artifact_digest=fingerprint,
                policy_version=resume_command.policy_version,
            )
        else:
            route = "rejected"
            reasons = [f"resumed with decision {resume_command.decision!r}"]
    elif not quality.passed:
        route = "rejected"
        reasons = [*quality.missing_fields, *quality.conflicts, *quality.invalid_values]
    elif ports.policy is not None:
        policy_decision = ports.policy.evaluate(
            PolicyRequest(
                decision_type="a1.request_approval",
                policy_version=payload.policy_version,
                tenant_id=_required_state_str(state, "tenant_id"),
                facts={
                    "actor_id": payload.actor_id,
                    "actor_role": payload.actor_role,
                    "request_fingerprint": fingerprint,
                    "repository_id": source.repository_id,
                    "feature_id": scope.feature_id,
                    "dirty": source.dirty,
                },
            )
        )
        approval_policy_version = policy_decision.policy_version
        reasons = list(policy_decision.reasons)
        if not policy_decision.allowed:
            route = "rejected"
        elif policy_decision.decision == "require_approval":
            route = "approval"
        elif policy_decision.decision == "allow":
            approval = ApprovalBinding(
                approval_id=f"{_required_state_str(state, 'case_id')}-A1-APPROVAL",
                actor_id=payload.actor_id,
                actor_role=payload.actor_role,
                decision="approve",
                artifact_digest=fingerprint,
                policy_version=approval_policy_version,
            )
        else:
            route = "rejected"
            reasons.append(f"unsupported policy decision {policy_decision.decision!r}")
    elif payload.actor_role not in {"owner", "approver", "platform_owner"}:
        route = "approval"
        reasons = ["actor role requires approval"]
    else:
        approval = ApprovalBinding(
            approval_id=f"{_required_state_str(state, 'case_id')}-A1-APPROVAL",
            actor_id=payload.actor_id,
            actor_role=payload.actor_role,
            decision="approve",
            artifact_digest=fingerprint,
            policy_version=approval_policy_version,
        )
    artifact = _seal(
        A1ApprovalDecision(
            **_base_envelope(
                state,
                "A1ApprovalDecision",
                parents=[
                    quality.content_digest,
                    objective.content_digest,
                    criteria.content_digest,
                    guardrails.content_digest,
                    workload.content_digest,
                    evidence.content_digest,
                    budget.content_digest,
                    source.content_digest,
                    scope.content_digest,
                ],
            ),
            approved=approval is not None,
            approval=approval,
            decision_route=cast("Any", route),
            reasons=reasons,
            policy_version=approval_policy_version,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.90")
    return _route_decision(state, ref, artifact.content_digest, route, approval_policy_version)


__all__ = ["handle_a1_90_evaluate_scope_data_risk_rbac_policy_create"]
