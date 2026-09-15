# pyright: reportPrivateUsage=false
"""Implementation of business node A1.61."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import (
    Criterion,
    CriterionSet,
    RawRequestDraft,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _acceptance_operator,
    _base_envelope,
    _criterion_id,
    _metric_profile,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a1_61_define_least_one_criterion_metric_direction_target(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """BR-A1-002: at least one primary measurable criterion.

    A request may declare several (`ManualCasePayload.criteria`), each with
    its own `weight` -- S01.40 ranks strategies by every criterion's
    weight-normalized benefit, so more than one is the general case and the
    four single-criterion shorthand fields are just the common one.
    `criterion_id` is derived from the metric so the ids A1.71, A3.20 and
    S01 build off it (`evidence-<id>`, `signal-<id>`) name the real metric
    rather than a positional index; the shorthand path keeps the historical
    `"primary"` id.
    """

    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    missing: list[str] = []
    criteria: list[Criterion] = []

    if draft.criteria:
        for declared in draft.criteria:
            metric = _metric_profile(
                declared.metric_id, unit=declared.unit, direction=declared.direction
            )
            criteria.append(
                Criterion(
                    criterion_id=_criterion_id(declared.metric_id),
                    metric_id=declared.metric_id,
                    direction=declared.direction,
                    target=declared.target,
                    unit=declared.unit,
                    weight=declared.weight,
                    aggregation=cast("Any", metric["aggregation"]),
                    acceptance_operator=_acceptance_operator(declared.direction),
                    metric_schema_version=cast("str", metric["schema_version"]),
                )
            )
    else:
        if not draft.metric_id:
            missing.append("metric_id")
        if draft.direction is None:
            missing.append("direction")
        if draft.target is None:
            missing.append("target")
        if not draft.unit:
            missing.append("unit")
        if not missing:
            metric = _metric_profile(
                draft.metric_id or "", unit=draft.unit, direction=draft.direction
            )
            criteria.append(
                Criterion(
                    criterion_id="primary",
                    metric_id=draft.metric_id or "",
                    direction=draft.direction or "target",
                    target=draft.target or 0.0,
                    unit=draft.unit or "",
                    weight=1.0,
                    aggregation=cast("Any", metric["aggregation"]),
                    acceptance_operator=_acceptance_operator(draft.direction),
                    metric_schema_version=cast("str", metric["schema_version"]),
                )
            )
    artifact = _seal(
        CriterionSet(
            **_base_envelope(state, "CriterionSet", parents=[draft.content_digest]),
            criteria=criteria,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.61")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_61_define_least_one_criterion_metric_direction_target"]
