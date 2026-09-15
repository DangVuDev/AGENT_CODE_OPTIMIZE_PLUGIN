# pyright: reportPrivateUsage=false
"""Implementation of business node A2.10."""

from __future__ import annotations

from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import A2IntakeDecision
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
)


def _source_is_reachable(request: OptimizationRequest, ports: NodePorts) -> tuple[bool, str | None]:
    if request.source.source_kind == "local_directory":
        try:
            allowed_root = Path(request.source.allowed_root_id).resolve()
            canonical_path = (allowed_root / request.source.relative_path).resolve()
            reachable = canonical_path.is_relative_to(allowed_root) and canonical_path.is_dir()
        except (OSError, ValueError):
            reachable = False
        return (
            reachable,
            None if reachable else "local source path is unreachable or escapes its allowed root",
        )
    if ports.sources is None:
        return False, f"source kind {request.source.source_kind!r} requires a SourceProvider"
    if not request.source.locator:
        return False, "non-local source is missing its locator"
    reachable = ports.sources.healthcheck()
    return reachable, None if reachable else "source provider is unavailable"


def handle_a2_10_verify_request_schema_digest_signature_approval_policy(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    mismatches: list[str] = []
    if state.get("request_ref") != request_ref:
        mismatches.append("state request_ref does not match the OptimizationRequest artifact")
    if request_ref.artifact_type != "OptimizationRequest" or request_ref.schema_version != "1.0":
        mismatches.append("unsupported request artifact identity")
    if not ports.artifacts.verify(
        tenant_id=_required_state_str(state, "tenant_id"), ref=request_ref
    ):
        mismatches.append("request artifact digest verification failed")

    request = _read_model(ports, state, request_ref, OptimizationRequest)
    if request.tenant_id != _required_state_str(state, "tenant_id"):
        mismatches.append("request tenant does not match graph tenant")
    if request.case_id != _required_state_str(state, "case_id"):
        mismatches.append("request case does not match graph case")

    if request.approval.decision != "approve":
        mismatches.append("request is not approved")
    if request.approval.artifact_digest != request.request_fingerprint:
        mismatches.append("approval digest mismatch")

    source_reachable, source_error = _source_is_reachable(request, ports)
    if source_error:
        mismatches.append(source_error)

    decision = _seal(
        A2IntakeDecision(
            **_base_envelope(state, "A2IntakeDecision", parents=[request.content_digest]),
            verified=not mismatches and source_reachable,
            mismatches=mismatches,
            source_reachable=source_reachable,
            policy_version=request.approval.policy_version if request.approval else "unknown",
        )
    )
    ref = _put_envelope(ports, state, decision, node_id="A2.10")
    route = NodeRoute.CONTINUE if decision.verified else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_10_verify_request_schema_digest_signature_approval_policy"]
