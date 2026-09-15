# pyright: reportPrivateUsage=false
"""Implementation of business node A3.21."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import PrioritizedSignalSet, ProblemSignalSet
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a3_21_score_impact_weight_scope_frequency_severity_trust(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Deterministic priority score: breach magnitude (70%) + evidence weight (30%).

    A fixed, documented v1 heuristic (`policy_versions={"a3": "production-
    v1"}` on the envelope) rather than a `PolicyPort` call — `PolicyDecision`
    only carries `allowed`/`reasons`, no numeric score shape, so it cannot
    express a ranking. All signals are retained regardless of score, per the
    playbook ("Retain nonselected signals").
    """

    signal_set = _read_model(
        ports, state, _require_ref(state, "ProblemSignalSet"), ProblemSignalSet
    )

    scores: dict[str, float] = {}
    for signal in signal_set.signals:
        deviation = abs(signal.baseline_value - signal.target_value)
        denom = abs(signal.target_value) if signal.target_value != 0 else 1.0
        magnitude = min(deviation / denom, 10.0)
        evidence_weight = min(len(signal.evidence_ids), 5) / 5.0
        scores[signal.signal_id] = round(magnitude * 0.7 + evidence_weight * 0.3, 4)

    prioritized = _seal(
        PrioritizedSignalSet(
            **_base_envelope(state, "PrioritizedSignalSet", parents=[signal_set.content_digest]),
            signals=signal_set.signals,
            priority_scores=scores,
        )
    )
    ref = _put_envelope(ports, state, prioritized, node_id="A3.21")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_21_score_impact_weight_scope_frequency_severity_trust"]
