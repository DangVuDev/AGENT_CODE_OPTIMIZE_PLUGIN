# pyright: reportPrivateUsage=false
"""Implementation of business node C0.60."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.c0 import (
    ConvergenceDecision,
    DigestChainLink,
    DimensionEquivalenceVerdict,
    FreshnessCheck,
    SchemaValidationResult,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _origin,
    _put_envelope,
    _read_required,
    _seal,
)


def handle_c0_60_require_comparable_baseline_passed_a3_quality_least(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """The convergence gate itself. `ConvergenceDecision`'s own validator
    (`contracts/c0.py`) pins `converged` to exactly this AND across the four
    gate categories plus `eligible_solution_count >= 1` -- this handler
    computes the same formula for real from C0.10-50's actual results
    rather than asserting it, so a real failure anywhere upstream routes
    REJECTED here instead of silently sealing a false `converged=True`."""

    schema_results = cast("list[SchemaValidationResult]", state.get("c0_schema_results") or [])
    digest_chain = cast("list[DigestChainLink]", state.get("c0_digest_chain") or [])
    equivalence_verdicts = cast(
        "list[DimensionEquivalenceVerdict]", state.get("c0_equivalence_verdicts") or []
    )
    freshness_checks = cast("list[FreshnessCheck]", state.get("c0_freshness_checks") or [])

    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    eligible_solution_count = sum(1 for strategy in portfolio.strategies if strategy.eligible)

    schema_ok = all(result.valid for result in schema_results)
    digest_ok = all(link.linked for link in digest_chain)
    equivalence_ok = all(verdict.equivalent for verdict in equivalence_verdicts)
    freshness_ok = all(check.fresh for check in freshness_checks)
    eligible_ok = eligible_solution_count >= 1
    converged = schema_ok and digest_ok and equivalence_ok and freshness_ok and eligible_ok

    reasons: list[str] = []
    if not schema_ok:
        reasons.append("one or more artifacts failed schema validation")
    if not digest_ok:
        reasons.append("digest chain is broken between one or more artifacts")
    if not equivalence_ok:
        reasons.append("a semantic equivalence check failed")
    if not freshness_ok:
        reasons.append("a freshness check failed")
    if not eligible_ok:
        reasons.append("no eligible solution strategy is available")

    decision = _seal(
        ConvergenceDecision(
            **_base_envelope(state, "ConvergenceDecision"),
            origin=cast("Any", _origin(state)),
            schema_results=schema_results,
            digest_chain=digest_chain,
            equivalence_verdicts=equivalence_verdicts,
            freshness_checks=freshness_checks,
            eligible_solution_count=eligible_solution_count,
            converged=converged,
            reasons=reasons,
        )
    )
    ref = _put_envelope(ports, state, decision, node_id="C0.60")
    route = NodeRoute.CONTINUE if converged else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


__all__ = ["handle_c0_60_require_comparable_baseline_passed_a3_quality_least"]
