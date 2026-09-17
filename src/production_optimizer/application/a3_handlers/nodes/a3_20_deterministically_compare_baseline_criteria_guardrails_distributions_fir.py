# pyright: reportPrivateUsage=false
"""Implementation of business node A3.20."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from production_optimizer.application.metrics import resolve_metric, select_aggregate_value
from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import Criterion, OptimizationRequest
from production_optimizer.contracts.a2 import BaselineSnapshot, EvidenceBundle, EvidenceItem
from production_optimizer.contracts.a3 import EvidenceCatalog, ProblemSignal, ProblemSignalSet
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _GUARDRAIL_OPERATORS,
    _base_envelope,
    _criterion_breached,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def _grounding_suffix(evidence_ids: list[str], *, precise_ids: list[str]) -> str:
    """A signal's `evidence_ids` can come from three different tiers of
    confidence: (1) evidence that numerically confirms the breach, (2)
    evidence for the same metric_id that exists but wasn't numerically
    confirmed (e.g. a non-numeric `value` like "unmeasured"), or (3) raw
    aggregate sample_ids with no per-item evidence at all. Silently
    treating tier 2/3 the same as tier 1 would let a signal look
    precisely evidence-grounded when it isn't -- this labels the weaker
    tiers directly in the description (a free-text field already read by
    downstream reports/reviewers) rather than adding a new schema field."""

    if evidence_ids and evidence_ids is precise_ids:
        return ""
    if not evidence_ids:
        return " [evidence: none available]"
    return " [evidence: same-metric, not numerically confirmed]"


def _numeric_value(item: EvidenceItem) -> float | None:
    value = item.value
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    return None


def _criterion_supporting_evidence_ids(
    bundle: EvidenceBundle, criterion: Criterion
) -> list[str]:
    ids: list[str] = []
    for item in bundle.evidence:
        if item.metric_id != criterion.metric_id:
            continue
        value = _numeric_value(item)
        if value is not None and _criterion_breached(criterion, value):
            ids.append(item.evidence_id)
    return ids


def _guardrail_supporting_evidence_ids(
    bundle: EvidenceBundle,
    metric_id: str,
    operator: Callable[[float, float], bool],
    threshold: float,
) -> list[str]:
    ids: list[str] = []
    for item in bundle.evidence:
        if item.metric_id != metric_id:
            continue
        value = _numeric_value(item)
        if value is not None and not operator(value, threshold):
            ids.append(item.evidence_id)
    return ids


def handle_a3_20_deterministically_compare_baseline_criteria_guardrails_distributions_fir(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Compare `BaselineSnapshot` against criteria/guardrails.

    Today's A2 collectors only ever produce `metric_id`s like
    `unit_command_result`/`lint_command_result`/`type_command_result`/
    `source_map` (see `a2_handlers.py`'s `_EVIDENCE_TYPE_SOURCE_TYPE`) — real
    command exit codes and a file-map count, never an arbitrary business
    metric like `p95_latency_ms` (A2.62/A2.63 always report
    `unavailable_reason`). A `Criterion` naming such a metric legitimately
    finds zero matching aggregate here and produces no signal for it — an
    honest `NO_ACTIONABLE_PROBLEM` outcome, not a bug. Guardrails keyed to
    A2's real command metrics (e.g. a correctness guardrail on
    `unit_command_result`) fire correctly.
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    baseline = _read_model(ports, state, _require_ref(state, "BaselineSnapshot"), BaselineSnapshot)
    catalog = _read_model(ports, state, _require_ref(state, "EvidenceCatalog"), EvidenceCatalog)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)

    aggregates_by_metric = {agg.metric_id: agg for agg in baseline.aggregates}
    now = datetime.now(UTC)
    signals: list[ProblemSignal] = []

    for criterion in request.criteria:
        agg = aggregates_by_metric.get(criterion.metric_id)
        if agg is None:
            continue
        observed = select_aggregate_value(agg, criterion.aggregation)
        if not _criterion_breached(criterion, observed):
            continue
        precise_ids = _criterion_supporting_evidence_ids(bundle, criterion)
        evidence_ids = precise_ids or catalog.by_metric.get(criterion.metric_id) or list(
            agg.sample_ids
        )
        signals.append(
            ProblemSignal(
                signal_id=f"signal-{criterion.criterion_id}",
                criterion_id=criterion.criterion_id,
                metric_id=criterion.metric_id,
                signal_kind="threshold_breach",
                description=(
                    f"{criterion.metric_id} {criterion.aggregation} {observed} breaches "
                    f"{criterion.direction} target {criterion.target}"
                    f"{_grounding_suffix(evidence_ids, precise_ids=precise_ids)}"
                ),
                baseline_value=observed,
                target_value=criterion.target,
                unit=criterion.unit,
                evidence_ids=evidence_ids,
                detected_at=now,
            )
        )

    for guardrail in request.guardrails:
        agg = aggregates_by_metric.get(guardrail.metric_id)
        if agg is None:
            continue
        definition = resolve_metric(
            guardrail.metric_id,
            declared_unit=guardrail.unit,
            declared_direction="target",
        )
        observed = select_aggregate_value(agg, definition.aggregation)
        op = _GUARDRAIL_OPERATORS[guardrail.operator]
        if op(observed, guardrail.threshold):
            continue
        precise_ids = _guardrail_supporting_evidence_ids(
            bundle, guardrail.metric_id, op, guardrail.threshold
        )
        evidence_ids = precise_ids or catalog.by_metric.get(guardrail.metric_id) or list(
            agg.sample_ids
        )
        signals.append(
            ProblemSignal(
                signal_id=f"signal-guardrail-{guardrail.guardrail_id}",
                # ProblemSignal has no dedicated guardrail field; reusing
                # criterion_id to carry guardrail_id is a pragmatic choice,
                # not a claim the guardrail is a criterion.
                criterion_id=guardrail.guardrail_id,
                metric_id=guardrail.metric_id,
                signal_kind="threshold_breach",
                description=(
                    f"guardrail {guardrail.guardrail_id} violated: "
                    f"{guardrail.metric_id} {definition.aggregation} {observed} fails "
                    f"{guardrail.operator} {guardrail.threshold}"
                    f"{_grounding_suffix(evidence_ids, precise_ids=precise_ids)}"
                ),
                baseline_value=observed,
                target_value=guardrail.threshold,
                unit=guardrail.unit,
                evidence_ids=evidence_ids,
                detected_at=now,
            )
        )

    signal_set = _seal(
        ProblemSignalSet(
            **_base_envelope(
                state,
                "ProblemSignalSet",
                parents=[baseline.content_digest, catalog.content_digest, bundle.content_digest],
            ),
            signals=signals,
        )
    )
    ref = _put_envelope(ports, state, signal_set, node_id="A3.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_20_deterministically_compare_baseline_criteria_guardrails_distributions_fir"]
