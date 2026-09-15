# pyright: reportPrivateUsage=false
"""Implementation of business node A2.40."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import CollectorBinding, CollectorPlan
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _COLLECTOR_CATALOG_VERSION,
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _resolve_binding,
    _seal,
)


def handle_a2_40_bind_every_evidence_requirement_collector_query_command(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)

    bindings: list[CollectorBinding] = []
    unresolved: list[str] = []
    registry_versions: dict[str, int] = {}

    for requirement in request.evidence_requirements:
        resolved = _resolve_binding(requirement, ports=ports, state=state)
        if resolved is None:
            unresolved.append(requirement.requirement_id)
            continue
        binding, registry_version = resolved
        bindings.append(binding)
        if registry_version is not None:
            registry_versions[binding.recipe_id] = registry_version

    plan = _seal(
        CollectorPlan(
            **_base_envelope(state, "CollectorPlan", parents=[request.content_digest]),
            bindings=bindings,
            unresolved_requirements=unresolved,
            catalog_version=_COLLECTOR_CATALOG_VERSION,
            registry_record_versions=registry_versions,
        )
    )
    ref = _put_envelope(ports, state, plan, node_id="A2.40")
    mandatory_ids = {
        requirement.requirement_id
        for requirement in request.evidence_requirements
        if requirement.mandatory
    }
    unresolved_mandatory = sorted(mandatory_ids & set(unresolved))
    if not unresolved_mandatory:
        return NodeExecution(updates={"artifact_refs": [ref]})
    interrupt = InterruptEnvelope(
        interrupt_id=f"{_required_state_str(state, 'case_id')}-A2-EVIDENCE-REQUIRED",
        case_id=_required_state_str(state, "case_id"),
        thread_id=_required_state_str(state, "thread_id"),
        stage="A2.40",
        artifact_digest=plan.content_digest,
        allowed_decisions=["revise", "reject"],
        required_actor_role="owner",
        policy_version=request.approval.policy_version,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    return NodeExecution(
        route=NodeRoute.MISSING,
        updates={"artifact_refs": [ref], "pending_interrupt": interrupt},
    )


__all__ = ["handle_a2_40_bind_every_evidence_requirement_collector_query_command"]
