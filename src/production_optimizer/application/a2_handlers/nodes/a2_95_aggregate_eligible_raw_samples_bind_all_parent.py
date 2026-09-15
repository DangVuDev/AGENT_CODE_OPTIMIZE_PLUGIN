# pyright: reportPrivateUsage=false
"""Implementation of business node A2.95."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    BaselineSnapshot,
    ComparabilityReport,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQualityReport,
    MetricAggregate,
    SourceSnapshot,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a2_95_aggregate_eligible_raw_samples_bind_all_parent(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Aggregate eligible evidence into the sealed A3 handoff.

    Fail-closed gate per the playbook ("Complete only when quality and
    comparability are true"): refuses to publish unless both A2.90's
    `passed` and A2.91's `comparable` are true, rather than publishing a
    baseline the platform already knows is unreliable. The compiled graph
    (`orchestration/subgraphs/a2.py`) already routes away from this node to
    `END` on a failing A2.90/A2.91 route, so this `raise` is defense-in-depth
    for direct `NodeRuntime.execute("A2.95", ...)` calls that bypass the
    graph's conditional edges (as most of this file's tests do).
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    quality = _read_model(
        ports, state, _require_ref(state, "EvidenceQualityReport"), EvidenceQualityReport
    )
    comparability = _read_model(
        ports, state, _require_ref(state, "ComparabilityReport"), ComparabilityReport
    )

    if not quality.passed:
        raise ValueError("A2.95 cannot publish a baseline: EvidenceQualityReport did not pass")
    if not comparability.comparable:
        raise ValueError("A2.95 cannot publish a baseline: ComparabilityReport is not comparable")

    grouped: dict[tuple[str, str], list[EvidenceItem]] = {}
    for item in bundle.evidence:
        if (
            item.requirement_id is not None
            and item.metric_id is not None
            and item.unit is not None
            and isinstance(item.value, int | float)
        ):
            grouped.setdefault((item.metric_id, item.unit), []).append(item)

    aggregates: list[MetricAggregate] = []
    for (metric_id, unit), items in sorted(grouped.items()):
        values = sorted(float(cast("float", item.value)) for item in items)
        rank_95 = max(0, math.ceil(len(values) * 0.95) - 1)
        rank_50 = max(0, math.ceil(len(values) * 0.50) - 1)
        aggregates.append(
            MetricAggregate(
                metric_id=metric_id,
                unit=unit,
                sample_ids=[item.identity.sample_id for item in items],
                count=len(values),
                minimum=values[0],
                maximum=values[-1],
                mean=sum(values) / len(values),
                percentile_50=values[rank_50],
                percentile_95=values[rank_95],
            )
        )

    observed_times = [item.identity.observed_at for item in bundle.evidence]
    window_start = min(observed_times)
    window_end = max(observed_times)
    if window_end <= window_start:
        window_end = window_start + timedelta(microseconds=1)

    baseline = _seal(
        BaselineSnapshot(
            **_base_envelope(
                state,
                "BaselineSnapshot",
                parents=[
                    request.content_digest,
                    snapshot.content_digest,
                    bundle.content_digest,
                    quality.content_digest,
                    comparability.content_digest,
                ],
            ),
            request_digest=request.content_digest,
            source_snapshot_digest=snapshot.content_digest,
            workload_id=request.workload.workload_id,
            environment_id=request.workload.environment_id,
            window_start=window_start,
            window_end=window_end,
            aggregates=aggregates,
        )
    )
    ref = _put_envelope(ports, state, baseline, node_id="A2.95")
    return NodeExecution(updates={"artifact_refs": [ref], "baseline_ref": ref})


__all__ = ["handle_a2_95_aggregate_eligible_raw_samples_bind_all_parent"]
