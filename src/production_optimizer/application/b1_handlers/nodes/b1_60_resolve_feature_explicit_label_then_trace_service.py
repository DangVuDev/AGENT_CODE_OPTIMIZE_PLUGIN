# pyright: reportPrivateUsage=false
"""Implementation of business node B1.60."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import DetectionSignal, FeatureBinding
from production_optimizer.contracts.state import OptimizationState


def handle_b1_60_resolve_feature_explicit_label_then_trace_service(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    bindings = [
        FeatureBinding(
            binding_id=f"feature-binding-{signal.signal_id}",
            signal_ids=[signal.signal_id],
            feature_id=None,
            confidence=0.0,
            method="explicit_label",
            resolved=False,
        )
        for signal in signals
    ]
    return NodeExecution(updates={"b1_feature_bindings": bindings})


__all__ = ["handle_b1_60_resolve_feature_explicit_label_then_trace_service"]
