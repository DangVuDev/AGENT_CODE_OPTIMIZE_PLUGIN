# pyright: reportPrivateUsage=false
"""Implementation of business node B1.70."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import DetectionSignal, OpportunityScore
from production_optimizer.contracts.state import OptimizationState


def handle_b1_70_deterministically_score_severity_frequency_impact_trust_addressability(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    scores = [
        OpportunityScore(
            score_id=f"score-{signal.signal_id}",
            severity=signal.severity,
            frequency=min(1.0, len(signal.run_group_ids) / 10),
            business_impact=signal.severity,
            evidence_trust=0.5,
            addressability=0.5,
            strategic_priority=0.5,
            # Same weighting judgment as A3.21's priority score
            # (`a3_handlers.py`): 70% observed severity, 30% how much
            # evidence actually backs it.
            composite=round(0.7 * signal.severity + 0.3 * 0.5, 4),
            policy_version="b1-score-v1",
        )
        for signal in signals
    ]
    return NodeExecution(updates={"b1_scores": scores})


__all__ = ["handle_b1_70_deterministically_score_severity_frequency_impact_trust_addressability"]
