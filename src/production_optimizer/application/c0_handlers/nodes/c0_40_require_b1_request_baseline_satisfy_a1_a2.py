# pyright: reportPrivateUsage=false
"""Implementation of business node C0.40."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import BaselineSnapshot
from production_optimizer.contracts.a3 import A3QualityReport
from production_optimizer.contracts.c0 import DimensionEquivalenceVerdict
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_required,
)


def handle_c0_40_require_b1_request_baseline_satisfy_a1_a2(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real semantic equivalence (per doc row: "Require B1 request/baseline
    to satisfy A1/A2 contracts and B2 to satisfy A3 policy"). Two checks,
    both lane-agnostic since B1/B2 embed the real A2/A3 graphs:

    (1) the baseline was actually collected for the workload/environment
    the request specified, not some other one. This is *not* a check that
    every `Criterion.metric_id` was measured: A2's aggregates are keyed by
    `EvidenceItem.evidence_type` (a2_handlers._a2_95), a raw collector
    label such as "unit_command_result", which was never designed to equal
    a criterion's business metric id -- a performance criterion can
    legitimately have zero matching evidence today (no benchmark collector
    is wired in yet; see `scripts/optimize.py`'s own `--metric` help text)
    without that being a convergence failure.
    (2) A3's own quality gate must have actually passed.
    """

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    baseline = cast("BaselineSnapshot", _read_required(ports, state, "BaselineSnapshot"))
    quality = cast("A3QualityReport", _read_required(ports, state, "A3QualityReport"))

    workload_ok = (
        baseline.workload_id == request.workload.workload_id
        and baseline.environment_id == request.workload.environment_id
    )

    verdicts = [
        DimensionEquivalenceVerdict(
            dimension="request_baseline_contract",
            equivalent=workload_ok,
            reason=(
                None
                if workload_ok
                else (
                    f"baseline was collected for workload={baseline.workload_id!r}/"
                    f"environment={baseline.environment_id!r}, but the request specified "
                    f"workload={request.workload.workload_id!r}/"
                    f"environment={request.workload.environment_id!r}"
                )
            ),
        ),
        DimensionEquivalenceVerdict(
            dimension="solution_quality_policy",
            equivalent=quality.passed,
            reason=None if quality.passed else "A3QualityReport did not pass its own quality gates",
        ),
    ]
    return NodeExecution(updates={"c0_equivalence_verdicts": verdicts})


__all__ = ["handle_c0_40_require_b1_request_baseline_satisfy_a1_a2"]
