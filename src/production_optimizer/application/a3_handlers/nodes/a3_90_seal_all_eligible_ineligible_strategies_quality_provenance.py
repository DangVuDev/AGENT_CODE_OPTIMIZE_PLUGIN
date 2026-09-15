# pyright: reportPrivateUsage=false
"""Implementation of business node A3.90."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    FindingSet,
    SolutionPortfolio,
    SolutionStrategySet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
)


def handle_a3_90_seal_all_eligible_ineligible_strategies_quality_provenance(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    current_pass = _revision_pass(state)
    finding_set = _read_model(ports, state, _require_ref(state, "FindingSet"), FindingSet)
    strategy_ref = _require_stage_ref(
        state, _pass_stage_id("A3.80", current_pass), "SolutionStrategySet"
    )
    strategy_set = _read_model(ports, state, strategy_ref, SolutionStrategySet)
    quality_ref = _require_stage_ref(
        state, _pass_stage_id("A3.81", current_pass), "A3QualityReport"
    )
    quality = _read_model(ports, state, quality_ref, A3QualityReport)

    portfolio = _seal(
        SolutionPortfolio(
            **_base_envelope(
                state,
                "SolutionPortfolio",
                parents=[
                    finding_set.content_digest,
                    strategy_set.content_digest,
                    quality.content_digest,
                ],
            ),
            finding_set_digest=finding_set.content_digest,
            strategies=strategy_set.strategies,
            quality_report_digest=quality.content_digest,
        )
    )
    ref = _put_envelope(ports, state, portfolio, node_id="A3.90")
    return NodeExecution(updates={"artifact_refs": [ref], "solution_portfolio_ref": ref})


__all__ = ["handle_a3_90_seal_all_eligible_ineligible_strategies_quality_provenance"]
