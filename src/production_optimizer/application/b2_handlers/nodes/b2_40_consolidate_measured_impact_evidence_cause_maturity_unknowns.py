# pyright: reportPrivateUsage=false
"""Implementation of business node B2.40."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _require_ref,
)


def handle_b2_40_consolidate_measured_impact_evidence_cause_maturity_unknowns(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Confirm every artifact B2.60 will need to seal `ProposalEnvelope`
    already exists -- no assembly happens here (see module docstring)."""

    del ports
    required_types = ("QualifiedOpportunity", "FindingSet", "SolutionPortfolio", "A3QualityReport")
    for artifact_type in required_types:
        _require_ref(state, artifact_type)
    return NodeExecution()


__all__ = ["handle_b2_40_consolidate_measured_impact_evidence_cause_maturity_unknowns"]
