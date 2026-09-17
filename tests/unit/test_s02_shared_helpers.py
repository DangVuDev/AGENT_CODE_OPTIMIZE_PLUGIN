# pyright: reportPrivateUsage=false
"""Exercises S02's pure `_criterion_coverage` helper directly -- it decides
whether a plan actually validates each criterion the strategy claims to
affect, and deserves a focused test rather than only being reachable
through a full S02.30->S02.60 pipeline run with a scripted LLM."""

from __future__ import annotations

from dataclasses import dataclass, field

from production_optimizer.application.s02_handlers.shared import _criterion_coverage
from production_optimizer.contracts.a1 import Criterion
from production_optimizer.contracts.a3 import CriterionImpact, ImpactAssessment
from production_optimizer.contracts.s02 import ExecutionPhase, PlanTreatment


def _phase(
    *,
    phase_id: str,
    affected_criteria: list[str],
    done_criteria: list[str],
    validation_command_ids: list[str] | None = None,
) -> ExecutionPhase:
    return ExecutionPhase(
        phase_id=phase_id,
        sequence=1,
        phase_kind="implementation",
        risk_tier="code",
        treatment=PlanTreatment(variable="x", before="1", after="2"),
        done_criteria=done_criteria,
        affected_criteria=affected_criteria,
        validation_command_ids=(
            ["cmd-1"] if validation_command_ids is None else validation_command_ids
        ),
        rollback_trigger="metric regresses",
        rollback_deadline_seconds=600,
    )


@dataclass
class _StubStrategy:
    """A minimal stand-in for `SolutionStrategy` -- `_criterion_coverage`
    only ever reads `.impact_assessment.criterion_impacts`."""

    impact_assessment: ImpactAssessment


def _strategy(criterion_ids: list[str]) -> _StubStrategy:
    return _StubStrategy(
        impact_assessment=ImpactAssessment(
            assessment_id="impact-1",
            strategy_id="strategy-1",
            criterion_impacts=[
                CriterionImpact(
                    criterion_id=criterion_id,
                    direction="improves",
                    confidence=0.9,
                    basis="forecast",
                )
                for criterion_id in criterion_ids
            ],
        )
    )


def _criterion(criterion_id: str, *, metric_id: str, target: float = 100.0) -> Criterion:
    return Criterion(
        criterion_id=criterion_id,
        metric_id=metric_id,
        direction="minimize",
        target=target,
        unit="ms",
        weight=1.0,
    )


@dataclass
class _StubRequest:
    criteria: list[Criterion] = field(default_factory=list["Criterion"])


def test_criterion_coverage_does_not_credit_an_unrelated_phase() -> None:
    """The bug this guards against: phase-1 affects `perf-1` but its own
    done_criteria text never mentions the metric/target; an unrelated
    phase-2 (which does not list `perf-1` in affected_criteria) happens to
    contain matching metric_id/target wording. Coverage for `perf-1` must
    stay False -- phase-2's wording must never leak in."""

    phase_1 = _phase(
        phase_id="phase-1",
        affected_criteria=["perf-1"],
        done_criteria=["the change is applied correctly"],
    )
    phase_2 = _phase(
        phase_id="phase-2",
        affected_criteria=[],
        done_criteria=["perf-1 latency_ms minimize 100.0"],
    )
    strategy = _strategy(["perf-1"])
    request = _StubRequest(criteria=[_criterion("perf-1", metric_id="latency_ms")])

    coverage = _criterion_coverage([phase_1, phase_2], strategy, request)  # type: ignore[arg-type]

    assert coverage["perf-1"] is False


def test_criterion_coverage_credits_a_phase_that_actually_covers_its_own_criterion() -> None:
    phase_1 = _phase(
        phase_id="phase-1",
        affected_criteria=["perf-1"],
        done_criteria=["perf-1 latency_ms minimize 100.0"],
    )
    strategy = _strategy(["perf-1"])
    request = _StubRequest(criteria=[_criterion("perf-1", metric_id="latency_ms")])

    coverage = _criterion_coverage([phase_1], strategy, request)  # type: ignore[arg-type]

    assert coverage["perf-1"] is True


def test_criterion_coverage_false_when_no_phase_affects_the_criterion() -> None:
    phase_1 = _phase(
        phase_id="phase-1", affected_criteria=[], done_criteria=["something unrelated"]
    )
    strategy = _strategy(["perf-1"])
    request = _StubRequest(criteria=[_criterion("perf-1", metric_id="latency_ms")])

    coverage = _criterion_coverage([phase_1], strategy, request)  # type: ignore[arg-type]

    assert coverage["perf-1"] is False


def test_criterion_coverage_false_when_no_validation_command_bound() -> None:
    phase_1 = _phase(
        phase_id="phase-1",
        affected_criteria=["perf-1"],
        done_criteria=["perf-1 latency_ms minimize 100.0"],
        validation_command_ids=[],
    )
    strategy = _strategy(["perf-1"])
    request = _StubRequest(criteria=[_criterion("perf-1", metric_id="latency_ms")])

    coverage = _criterion_coverage([phase_1], strategy, request)  # type: ignore[arg-type]

    assert coverage["perf-1"] is False
