# pyright: reportPrivateUsage=false
"""Implementation of business node A1.10."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import IntakeEnvelope, ManualCasePayload
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _input_mode,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_a1_10_authenticate_command_validate_tenant_case_thread_idempotency(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    payload_ref = _require_ref(state, "ManualCasePayload")
    payload = _read_model(ports, state, payload_ref, ManualCasePayload)
    actor = ActorContext.model_validate(state.get("actor_context"))
    if actor.tenant_id != _required_state_str(state, "tenant_id"):
        raise ValueError("authenticated actor tenant does not match the case tenant")
    if actor.actor_id != payload.actor_id:
        raise ValueError("payload actor_id does not match the authenticated actor")
    if payload.actor_role not in actor.roles:
        raise ValueError("payload actor_role is not granted to the authenticated actor")
    if actor.authenticated_at.tzinfo is None:
        raise ValueError("authenticated_at must be timezone-aware")

    envelope = _seal(
        IntakeEnvelope(
            **_base_envelope(state, "IntakeEnvelope", parents=[payload_ref.content_digest]),
            input_mode=_input_mode(payload),
            actor_id=payload.actor_id,
            actor_role=payload.actor_role,
            local_path=payload.local_path,
            allowed_root=payload.allowed_root,
            original_payload_digest=payload_ref.content_digest,
        )
    )
    ref = _put_envelope(ports, state, envelope, node_id="A1.10")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_10_authenticate_command_validate_tenant_case_thread_idempotency"]
