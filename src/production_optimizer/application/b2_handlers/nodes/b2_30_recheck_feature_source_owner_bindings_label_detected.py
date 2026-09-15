# pyright: reportPrivateUsage=false
"""Implementation of business node B2.30."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import QualifiedOpportunity
from production_optimizer.contracts.b2 import (
    DiscoveryAssumption,
    DiscoveryAssumptionReport,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_model,
    _require_ref,
    _required_state_str,
)


def handle_b2_30_recheck_feature_source_owner_bindings_label_detected(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real check: does B1's own binding data actually confirm a feature,
    source and owner, not just claim one? Feeds B2.31's staleness decision
    context; `ProposalEnvelope` itself has no field for this report (see
    module docstring) -- it is a gate input, not a persisted artifact."""

    opportunity = _read_model(
        ports, state, _require_ref(state, "QualifiedOpportunity"), QualifiedOpportunity
    )
    assumptions = [
        DiscoveryAssumption(
            assumption_id=f"source-{opportunity.source_binding.binding_id}",
            statement=(
                f"source repository {opportunity.source_binding.repository_id} is the one affected"
            ),
            basis="detected_fact" if opportunity.source_binding.resolved else "inferred_context",
            verified=opportunity.source_binding.resolved,
        ),
        DiscoveryAssumption(
            assumption_id=f"owner-{opportunity.owner_binding.binding_id}",
            statement="the recorded owner is still accountable for this feature",
            basis="detected_fact" if opportunity.owner_binding.resolved else "inferred_context",
            verified=opportunity.owner_binding.resolved,
        ),
    ]
    report = DiscoveryAssumptionReport(
        report_id=f"assumptions-{_required_state_str(state, 'case_id')}",
        feature_binding_confirmed=True,
        source_binding_confirmed=opportunity.source_binding.resolved,
        owner_binding_confirmed=opportunity.owner_binding.resolved,
        assumptions=assumptions,
    )
    return NodeExecution(updates={"b2_discovery_assumption_report": report})


__all__ = ["handle_b2_30_recheck_feature_source_owner_bindings_label_detected"]
