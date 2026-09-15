# pyright: reportPrivateUsage=false
"""Implementation of business node A1.60."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import (
    CanonicalObjective,
    FeatureScope,
    Objective,
    RawRequestDraft,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a1_60_separate_objective_requester_remedy_retain_remedy_hypothesis(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    scope = _read_model(ports, state, _require_ref(state, "FeatureScope"), FeatureScope)
    missing: list[str] = []
    objective: Objective | None = None
    if not draft.objective_statement:
        missing.append("objective_statement")
    if scope.confidence <= 0 or scope.feature_id == "unknown-feature":
        missing.append("feature_id")
    if not missing:
        objective = Objective(
            statement=draft.objective_statement or "",
            feature_id=scope.feature_id,
        )
    artifact = _seal(
        CanonicalObjective(
            **_base_envelope(
                state,
                "CanonicalObjective",
                parents=[draft.content_digest, scope.content_digest],
            ),
            objective=objective,
            requester_hypothesis=draft.requester_hypothesis,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.60")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_60_separate_objective_requester_remedy_retain_remedy_hypothesis"]
