# pyright: reportPrivateUsage=false
"""Implementation of business node B1.41."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState


def handle_b1_41_apply_schema_source_freshness_trust_minimum_sample(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Eligibility is informational only here: `subgraphs/b1.py` wires B1.41
    as a fan-out source, not a routed gate, so an ineligible batch simply
    carries zero eligible run groups into the detector fan-out -- which
    then correctly finds nothing, the same outcome an empty query result
    already produces. No state change; nothing to seal without a
    `EligibleRunSet` contract, which nothing downstream currently needs."""

    del state, ports
    return NodeExecution()


__all__ = ["handle_b1_41_apply_schema_source_freshness_trust_minimum_sample"]
