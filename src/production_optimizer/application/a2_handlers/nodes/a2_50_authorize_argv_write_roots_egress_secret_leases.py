# pyright: reportPrivateUsage=false
"""Implementation of business node A2.50."""

from __future__ import annotations

from dataclasses import dataclass

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    CollectorBinding,
    CollectorPlan,
    EnvironmentManifest,
    ExecutionAuthorization,
    SourceSnapshot,
    VerificationManifest,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import PolicyRequest, WorkerJob
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
    _worker_job_id,
)


@dataclass(frozen=True)
class _AuthorizationContext:
    ports: NodePorts
    tenant_id: str
    policy_version: str
    case_id: str
    timeout_seconds: int


@dataclass(frozen=True)
class _JobRequest:
    action_id: str
    capability: str | None
    facts: dict[str, object]
    input_refs: list[ArtifactRef]
    decision_type: str = "a2.execution_authorization"


def _authorize_job(
    context: _AuthorizationContext, job: _JobRequest
) -> tuple[bool, str | None, str | None]:
    if context.ports.policy is None or context.ports.workers is None:
        raise RuntimeError("A2.50 requires PolicyPort and WorkerBroker wired into NodePorts")
    decision = context.ports.policy.evaluate(
        PolicyRequest(
            decision_type=job.decision_type,
            policy_version=context.policy_version,
            tenant_id=context.tenant_id,
            facts=job.facts,
        )
    )
    if not decision.allowed:
        return False, decision.decision, None
    if job.capability is None:
        return True, None, None
    job_id = _worker_job_id(context.case_id, job.action_id)
    receipt = context.ports.workers.submit(
        WorkerJob(
            job_id=job_id,
            case_id=context.case_id,
            node_id="A2.50",
            idempotency_key=job_id,
            input_refs=job.input_refs,
            capability=job.capability,
            timeout_seconds=max(1, context.timeout_seconds),
            secret_refs=[],
        )
    )
    return (
        receipt.accepted,
        None if receipt.accepted else "worker broker rejected submission",
        job_id,
    )


def _collector_facts(binding: CollectorBinding, plan: CollectorPlan) -> dict[str, object]:
    return {
        "collector_id": binding.collector_id,
        "source_type": binding.source_type,
        "recipe_id": binding.recipe_id,
        "recipe_version": binding.recipe_version,
        "registry_version": plan.registry_record_versions.get(binding.recipe_id),
        "executor_capability": binding.executor_capability,
    }


def handle_a2_50_authorize_argv_write_roots_egress_secret_leases(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Authorize repository-owned commands and submit worker jobs for them.

    Scope is deliberately narrow: only `VerificationManifest.commands` (real,
    tool-detected argv from A2.31) are authorized and dispatched through
    `ports.workers`. `CollectorPlan.bindings` (A2.40) are policy-checked too
    so a denial is visible, but no job is submitted for them — there is no
    analyzer/collector adapter yet (A2.60-A2.64 stay blocked) to consume such
    a job, and submitting one anyway would fabricate work that never runs.
    """

    if ports.policy is None or ports.workers is None:
        raise RuntimeError("A2.50 requires PolicyPort and WorkerBroker wired into NodePorts")

    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    verification = _read_model(
        ports, state, _require_ref(state, "VerificationManifest"), VerificationManifest
    )
    plan = _read_model(ports, state, _require_ref(state, "CollectorPlan"), CollectorPlan)
    env = _read_model(ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest)

    tenant_id = _required_state_str(state, "tenant_id")
    case_id = _required_state_str(state, "case_id")
    policy_version = request.approval.policy_version if request.approval else "unknown"
    context = _AuthorizationContext(
        ports, tenant_id, policy_version, case_id, request.budget.maximum_worker_seconds
    )

    denied: list[str] = []
    worker_job_ids: list[str] = []
    authorized_action_ids: list[str] = []
    denied_action_ids: list[str] = []

    snapshot_ref = _require_ref(state, "SourceSnapshot")
    verification_ref = _require_ref(state, "VerificationManifest")
    if request.execution is not None:
        action_id = "compose:evaluation"
        accepted, reason, job_id = _authorize_job(
            context,
            _JobRequest(
                action_id=action_id,
                capability="compose_evaluation",
                facts={
                    "argv": ["docker", "compose"],
                    "working_directory": "immutable-source-snapshot",
                    "kind": "compose_evaluation",
                    "timeout_seconds": request.budget.maximum_worker_seconds,
                },
                input_refs=[snapshot_ref, request_ref],
            ),
        )
        if accepted and job_id:
            worker_job_ids.append(job_id)
            authorized_action_ids.append(action_id)
        else:
            denied.append(f"compose evaluation: {reason}")
            denied_action_ids.append(action_id)
    for command in verification.commands:
        accepted, reason, job_id = _authorize_job(
            context,
            _JobRequest(
                action_id=command.command_id,
                capability=command.kind,
                facts={
                    "argv": command.argv,
                    "working_directory": command.working_directory,
                    "kind": command.kind,
                    "timeout_seconds": request.budget.maximum_worker_seconds,
                },
                input_refs=[snapshot_ref, verification_ref],
            ),
        )
        if accepted and job_id:
            worker_job_ids.append(job_id)
            authorized_action_ids.append(command.command_id)
        else:
            denied.append(f"{command.command_id}: {reason}")
            denied_action_ids.append(command.command_id)

    plan_ref = _require_ref(state, "CollectorPlan")
    env_ref = _require_ref(state, "EnvironmentManifest")
    for binding in plan.bindings:
        action_id = f"recipe:{binding.requirement_id}"
        accepted, reason, job_id = _authorize_job(
            context,
            _JobRequest(
                action_id=action_id,
                capability=binding.executor_capability,
                facts=_collector_facts(binding, plan),
                input_refs=[snapshot_ref, plan_ref, env_ref],
                decision_type="a2.collector_authorization",
            ),
        )
        if accepted and job_id:
            worker_job_ids.append(job_id)
            authorized_action_ids.append(action_id)
        elif not accepted:
            denied.append(f"{binding.requirement_id}: {reason}")
            denied_action_ids.append(
                action_id
                if reason == "worker broker rejected submission"
                else binding.requirement_id
            )

    authorization = _seal(
        ExecutionAuthorization(
            **_base_envelope(
                state,
                "ExecutionAuthorization",
                parents=[
                    snapshot.content_digest,
                    verification.content_digest,
                    plan.content_digest,
                    env.content_digest,
                ],
            ),
            authorized=not denied,
            denied_capabilities=denied,
            allowed_write_roots=[f"worker-output://{case_id}"],
            allowed_egress=[],
            worker_job_ids=worker_job_ids,
            policy_version=policy_version,
            authorized_action_ids=authorized_action_ids,
            denied_action_ids=denied_action_ids,
            source_read_only=True,
        )
    )
    ref = _put_envelope(ports, state, authorization, node_id="A2.50")
    route = NodeRoute.CONTINUE if authorization.authorized else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_50_authorize_argv_write_roots_egress_secret_leases"]
