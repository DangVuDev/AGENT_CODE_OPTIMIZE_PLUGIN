# pyright: reportPrivateUsage=false
"""Implementation of business node A3.81."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import TrustLevel
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    FindingSet,
    QualityGateResult,
    SolutionStrategySet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _MAX_REVISION_ATTEMPTS,
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
)


def handle_a3_81_apply_evidence_maturity_diversity_coverage_treatment_rollback(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Deterministic quality gate; routes `continue`/`revision`/`rejected`.

    `A3QualityReport.solution_portfolio_digest` digests the *candidate*
    `SolutionStrategySet` this node evaluated, not the final sealed
    `SolutionPortfolio` — that artifact does not exist until A3.90 runs,
    strictly after this node, so it cannot be referenced here without a
    circular dependency between the two envelopes' required digest fields.
    """

    current_pass = _revision_pass(state)
    finding_set = _read_model(ports, state, _require_ref(state, "FindingSet"), FindingSet)
    strategy_ref = _require_stage_ref(
        state, _pass_stage_id("A3.80", current_pass), "SolutionStrategySet"
    )
    strategy_set = _read_model(ports, state, strategy_ref, SolutionStrategySet)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )

    finding_gate_results = [
        QualityGateResult(
            dimension="finding_has_evidence",
            passed=bool(finding.supporting_evidence_ids),
            detail=finding.finding_id,
        )
        for finding in finding_set.findings
    ]
    strategy_gate_results = [
        QualityGateResult(
            dimension="strategy_eligible",
            passed=strategy.eligible,
            detail=(
                f"{strategy.strategy_id}: "
                f"{'; '.join(strategy.gate_reasons) if strategy.gate_reasons else 'ok'}"
            ),
        )
        for strategy in strategy_set.strategies
    ]
    cause_maturity_gate_results = [
        QualityGateResult(
            dimension="cause_maturity",
            passed=(finding.claim_type != "verified_cause" or finding.trust_level == TrustLevel.T4),
            detail=finding.finding_id,
        )
        for finding in finding_set.findings
    ]

    eligible_strategies = [strategy for strategy in strategy_set.strategies if strategy.eligible]
    distinct_tradeoffs = len({strategy.strategy_tradeoffs for strategy in eligible_strategies})
    portfolio_gate_results = [
        QualityGateResult(
            dimension="at_least_one_eligible_strategy",
            passed=len(eligible_strategies) >= 1,
            detail=f"{len(eligible_strategies)} eligible of {len(strategy_set.strategies)}",
        ),
        QualityGateResult(
            dimension="strategies_materially_different",
            passed=distinct_tradeoffs == len(eligible_strategies),
            detail="ok"
            if distinct_tradeoffs == len(eligible_strategies)
            else "duplicate strategy_tradeoffs text",
        ),
    ]

    passed = all(
        result.passed
        for result in (
            *finding_gate_results,
            *strategy_gate_results,
            *cause_maturity_gate_results,
            *portfolio_gate_results,
        )
    )

    report = _seal(
        A3QualityReport(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.81", current_pass),
                "A3QualityReport",
                parents=[finding_set.content_digest, strategy_set.content_digest],
            ),
            finding_set_digest=finding_set.content_digest,
            solution_portfolio_digest=strategy_set.content_digest,
            passed=passed,
            finding_gate_results=finding_gate_results,
            strategy_gate_results=strategy_gate_results,
            cause_maturity_gate_results=cause_maturity_gate_results,
            portfolio_gate_results=portfolio_gate_results,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="A3.81")

    tokens_so_far = state.get("a3_model_tokens_spent", 0)
    budget_exhausted = (
        current_pass >= _MAX_REVISION_ATTEMPTS
        or tokens_so_far >= request.budget.maximum_model_tokens
    )

    if passed:
        route = NodeRoute.CONTINUE
    elif budget_exhausted:
        route = NodeRoute.REJECTED
    else:
        route = NodeRoute.REVISION

    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_81_apply_evidence_maturity_diversity_coverage_treatment_rollback"]
