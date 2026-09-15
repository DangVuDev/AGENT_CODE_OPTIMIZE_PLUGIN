# pyright: reportPrivateUsage=false
"""Implementation of business node A1.62."""

from __future__ import annotations

from production_optimizer.application.metrics import resolve_metric
from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import Guardrail, GuardrailSet, RawRequestDraft
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a1_62_define_correctness_plus_relevant_security_compatibility_cost(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Freeze the correctness guardrail.

    `metric_id`/`operator`/`threshold`/`unit` are pinned to the exact shape
    A2's real unit-test collector produces (`evidence_type="unit_command_result"`,
    the command's raw process exit code — see `a2_handlers.py`'s
    `_run_command_evidence_branch`, `command_kinds=("unit",)`). A guardrail
    that names a metric A2 never actually emits would never fire, which is
    worse than useless — it would look like a real correctness check while
    silently checking nothing. `draft.guardrail_metric_id` stays available
    for a future per-request override; today it only ever carries the one
    default `_draft_from_payload` sets, which is this same value.
    """

    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    missing: list[str] = []
    if not draft.guardrail_metric_id:
        missing.append("correctness_guardrail")
    guardrails: list[Guardrail] = []
    if not missing:
        definition = resolve_metric(
            draft.guardrail_metric_id or "",
            declared_unit="exit_code",
            declared_direction="target",
        )
        guardrails.append(
            Guardrail(
                guardrail_id="correctness",
                metric_id=draft.guardrail_metric_id or "",
                operator="eq",
                threshold=0.0,
                unit=definition.canonical_unit,
                metric_schema_version=definition.schema_version,
            )
        )
    artifact = _seal(
        GuardrailSet(
            **_base_envelope(state, "GuardrailSet", parents=[draft.content_digest]),
            guardrails=guardrails,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.62")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_62_define_correctness_plus_relevant_security_compatibility_cost"]
