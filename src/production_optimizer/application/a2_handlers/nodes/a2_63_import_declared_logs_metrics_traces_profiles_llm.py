# pyright: reportPrivateUsage=false
"""Implementation of business node A2.63."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    BranchEvidenceRefs,
    CollectorPlan,
    EnvironmentManifest,
    ExecutionAuthorization,
    SourceSnapshot,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _branch_envelope,
    _collect_registered_recipe_evidence,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a2_63_import_declared_logs_metrics_traces_profiles_llm(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Always-unavailable telemetry branch: no telemetry query adapter exists.

    `CollectorPlan.bindings` (A2.40) can name a telemetry collector (see
    `_COLLECTOR_CATALOG`), but nothing in `NodePorts` can query it — there is
    no Prometheus/Loki/Tempo/OTel query client wired up. Recording that gap
    honestly (rather than staying unregistered) matches the platform rule
    that zero sources is a legitimate, visible outcome, not a missing node.
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    plan = _read_model(ports, state, _require_ref(state, "CollectorPlan"), CollectorPlan)
    environment = _read_model(
        ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest
    )
    authorization = _read_model(
        ports, state, _require_ref(state, "ExecutionAuthorization"), ExecutionAuthorization
    )
    evidence, coverage, unavailable = _collect_registered_recipe_evidence(
        state,
        ports,
        node_id="A2.63",
        request=request,
        snapshot=snapshot,
        environment=environment,
        authorization=authorization,
        plan=plan,
    )
    passive = [
        binding
        for binding in plan.bindings
        if binding.execution_node == "A2.63" and binding.executor_capability is None
    ]
    unavailable.extend(
        f"{binding.requirement_id}: no executor capability is registered" for binding in passive
    )
    assigned = [binding for binding in plan.bindings if binding.execution_node == "A2.63"]
    if not assigned:
        unavailable.append("no evidence recipe is assigned to A2.63")
    reason = "; ".join(unavailable) if unavailable else None

    branch = _seal(
        BranchEvidenceRefs(
            **_branch_envelope(
                state, "A2.63", parents=[snapshot.content_digest, plan.content_digest]
            ),
            branch_id="A2.63",
            branch_kind="telemetry",
            evidence=evidence,
            coverage=coverage,
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A2.63")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_63_import_declared_logs_metrics_traces_profiles_llm"]
