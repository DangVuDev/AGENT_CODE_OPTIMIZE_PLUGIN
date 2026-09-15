# pyright: reportPrivateUsage=false
"""Implementation of business node B1.81."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.b1 import (
    CooldownDecision,
    DetectionReport,
    DetectionSignal,
    FeatureBinding,
    OpportunityScore,
    OwnershipBinding,
    QualificationDecision,
    RunGroup,
    SourceBinding,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_b1_81_apply_cooldown_expiry_severity_material_change_override(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Cooldown gate, then seal the one real `DetectionReport` this whole
    detect trunk (B1.40-81) has been accumulating in plain state fields."""

    qualification_decisions = cast(
        "list[QualificationDecision]", state.get("b1_qualification_decisions", [])
    )
    qualified = any(decision.qualified for decision in qualification_decisions)
    cooldown = CooldownDecision(
        decision_id=f"cooldown-{_required_state_str(state, 'case_id')}", suppressed=False
    )

    if not qualified:
        return NodeExecution(route=NodeRoute.CLOSED, updates={"b1_cooldown_decisions": [cooldown]})

    scan_context_ref = _require_ref(state, "DiscoveryScanContext")
    now = datetime.now(UTC)
    report = _seal(
        DetectionReport(
            **_base_envelope(state, "DetectionReport", parents=[scan_context_ref.content_digest]),
            scan_id=_required_state_str(state, "case_id"),
            window_start=now - timedelta(hours=24),
            window_end=now,
            run_groups=cast("list[RunGroup]", state.get("b1_run_groups", [])),
            signals=cast("list[DetectionSignal]", state.get("b1_signals", [])),
            feature_bindings=cast("list[FeatureBinding]", state.get("b1_feature_bindings", [])),
            source_bindings=cast("list[SourceBinding]", state.get("b1_source_bindings", [])),
            ownership_bindings=cast(
                "list[OwnershipBinding]", state.get("b1_ownership_bindings", [])
            ),
            scores=cast("list[OpportunityScore]", state.get("b1_scores", [])),
            qualification_decisions=qualification_decisions,
            cooldown_decisions=[cooldown],
        )
    )
    ref = _put_envelope(ports, state, report, node_id="B1.81")
    return NodeExecution(
        route=NodeRoute.CONTINUE,
        updates={"artifact_refs": [ref], "b1_cooldown_decisions": [cooldown]},
    )


__all__ = ["handle_b1_81_apply_cooldown_expiry_severity_material_change_override"]
