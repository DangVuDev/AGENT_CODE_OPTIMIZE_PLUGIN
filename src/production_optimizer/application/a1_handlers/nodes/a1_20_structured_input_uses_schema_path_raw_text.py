# pyright: reportPrivateUsage=false
"""Implementation of business node A1.20."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import ManualCasePayload
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _draft_from_payload,
    _put_envelope,
    _read_model,
    _require_ref,
)


def handle_a1_20_structured_input_uses_schema_path_raw_text(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    draft = _draft_from_payload(state, ports, payload)
    ref = _put_envelope(ports, state, draft, node_id="A1.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_20_structured_input_uses_schema_path_raw_text"]
