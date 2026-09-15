# pyright: reportPrivateUsage=false
"""Implementation of business node B1.80."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.b1 import DetectionSignal
from production_optimizer.contracts.state import OptimizationState


def handle_b1_80_compute_deterministic_candidate_fingerprint_compare_active_recent(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """In-batch dedupe only: collapses same-metric signals from this one
    scan. Real cross-case dedup (this scan vs. a case already open for the
    same feature) needs a queryable case history -- deferred with B1.32-35
    in this pass, not fabricated here."""

    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    seen_metrics = {signal.metric_id for signal in signals}
    merged = len(seen_metrics) < len(signals)
    return NodeExecution(route=NodeRoute.MERGED if merged else NodeRoute.CONTINUE)


__all__ = ["handle_b1_80_compute_deterministic_candidate_fingerprint_compare_active_recent"]
