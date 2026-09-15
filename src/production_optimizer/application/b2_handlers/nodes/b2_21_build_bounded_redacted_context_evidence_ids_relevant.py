# pyright: reportPrivateUsage=false
"""Implementation of business node B2.21."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import EvidenceBundle
from production_optimizer.contracts.b2 import (
    AnalysisStrategy,
    ModelContextPackage,
    TruncationReport,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _OptimizationRequestLike,
    _read_model,
    _require_ref,
    _required_state_str,
)


def handle_b2_21_build_bounded_redacted_context_evidence_ids_relevant(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    strategy = cast("AnalysisStrategy | None", state.get("b2_analysis_strategy"))
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), _OptimizationRequestLike
    )
    evidence_ids = [item.evidence_id for item in bundle.evidence] or ["no-evidence-collected"]
    criteria_ids = [criterion["criterion_id"] for criterion in request.criteria] or ["unknown"]
    max_evidence = 50
    truncated = len(evidence_ids) > max_evidence
    package = ModelContextPackage(
        package_id=f"context-{_required_state_str(state, 'case_id')}",
        strategy_id=strategy.strategy_id if strategy is not None else "unknown",
        evidence_ids=evidence_ids[:max_evidence],
        criteria_ids=criteria_ids,
        truncation_report=TruncationReport(
            truncated=truncated,
            omitted_evidence_ids=evidence_ids[max_evidence:] if truncated else [],
            reason=f"more than {max_evidence} evidence items" if truncated else None,
        ),
    )
    return NodeExecution(updates={"b2_model_context_package": package})


__all__ = ["handle_b2_21_build_bounded_redacted_context_evidence_ids_relevant"]
