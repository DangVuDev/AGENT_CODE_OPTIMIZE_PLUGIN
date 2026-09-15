# pyright: reportPrivateUsage=false
"""Implementation of business node B1.31."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import ReadAuthorization
from production_optimizer.contracts.platform import PolicyRequest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _required_state_str,
    _seal,
)


def handle_b1_31_authorize_endpoint_query_template_parameters_time_range(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    if ports.policy is None:
        raise RuntimeError("B1.31 requires ports.policy to be non-None")

    tenant_id = _required_state_str(state, "tenant_id")
    decision = ports.policy.evaluate(
        PolicyRequest(
            decision_type="automatic_discovery_read",
            policy_version="b1-read-authorization-v1",
            tenant_id=tenant_id,
            facts={"scan_id": _required_state_str(state, "case_id")},
        )
    )
    authorization = _seal(
        ReadAuthorization(
            **_base_envelope(state, "ReadAuthorization"),
            scan_id=_required_state_str(state, "case_id"),
            allowed=decision.allowed,
            reasons=list(decision.reasons),
            authorized_query_kinds=(
                cast("list[Any]", ["metrics", "logs", "traces", "llm_evidence"])
                if decision.allowed
                else []
            ),
        )
    )
    ref = _put_envelope(ports, state, authorization, node_id="B1.31")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_b1_31_authorize_endpoint_query_template_parameters_time_range"]
