from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from production_optimizer.contracts.a2 import TrustLevel
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    AnalyzerObservation,
    CitationResolutionEntry,
    CitationResolutionReport,
    CriterionImpact,
    ExperimentPhaseTemplate,
    Finding,
    FindingJudgement,
    FindingSet,
    ImpactAssessment,
    ProblemSignal,
    QualityGateResult,
    RiskAssessment,
    RollbackPlan,
    ScopeResolutionEntry,
    ScopeResolutionReport,
    SolutionPortfolio,
    SolutionStrategy,
    TradeoffAnalysis,
    Treatment,
    ValidationPlan,
)
from production_optimizer.contracts.envelope import ProducerIdentity


def _digest(character: str = "a") -> str:
    return f"sha256:{character * 64}"


def _producer() -> ProducerIdentity:
    return ProducerIdentity(name="a3-service", version="1.0.0")


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _judgement(verdict: Literal["accept", "reject"] = "accept") -> FindingJudgement:
    return FindingJudgement(
        judge_id="JUDGE-1",
        finding_id="FIND-1",
        verdict=verdict,
        reasons=["symptom and evidence are consistent"],
        model_id="judge-model",
        model_version="1.0",
    )


def _finding(
    claim_type: Literal["observation", "hypothesis", "verified_cause"] = "observation",
    trust_level: TrustLevel = TrustLevel.T1,
    verdict: Literal["accept", "reject"] = "accept",
) -> Finding:
    return Finding(
        finding_id="FIND-1",
        problem_signal_ids=["SIG-1"],
        claim_type=claim_type,
        symptom="p95 latency exceeds guardrail",
        causal_claim="synchronous IO blocks the hot path",
        supporting_evidence_ids=["EVID-1"],
        confidence=0.6,
        trust_level=trust_level,
        judgement=_judgement(verdict),
        analyzer_coverage={"semantic": 0.8},
    )


def _phase_template() -> ExperimentPhaseTemplate:
    return ExperimentPhaseTemplate(
        phase_id="PHASE-1",
        sequence=1,
        phase_kind="implementation",
        treatment=Treatment(variable="cache_strategy", before="none", after="lru"),
    )


def _solution_strategy(
    eligible: bool = True, gate_reasons: list[str] | None = None
) -> SolutionStrategy:
    return SolutionStrategy(
        strategy_id="STRAT-1",
        finding_ids=["FIND-1"],
        title="Introduce LRU cache",
        mechanism="Cache repeated lookups to avoid synchronous IO",
        strategy_tradeoffs="Adds memory pressure for latency improvement",
        phase_templates=[_phase_template()],
        risk_ceiling="code",
        risk_assessment=RiskAssessment(
            assessment_id="RISK-1",
            strategy_id="STRAT-1",
            risk_tier="code",
            blast_radius="single service",
            reversibility="fast",
            uncertainty=0.2,
            migration_impact=False,
            security_impact=False,
        ),
        impact_assessment=ImpactAssessment(
            assessment_id="IMPACT-1",
            strategy_id="STRAT-1",
            criterion_impacts=[
                CriterionImpact(
                    criterion_id="CRIT-1",
                    direction="improves",
                    confidence=0.7,
                    basis="forecast",
                )
            ],
        ),
        tradeoff_analysis=TradeoffAnalysis(
            analysis_id="TRADE-1",
            strategy_id="STRAT-1",
            pros=["reduces p95 latency"],
            cons=["adds cache invalidation complexity"],
            effort="medium",
            uncertainty=0.3,
        ),
        validation_plan=ValidationPlan(
            plan_id="VALID-1",
            strategy_id="STRAT-1",
            benchmark_protocol="run workload WL-1 for 100 reps",
            stop_conditions=["error rate exceeds guardrail"],
        ),
        rollback_plan=RollbackPlan(
            plan_id="ROLLBACK-1",
            strategy_id="STRAT-1",
            mechanism="revert the cache-strategy commit",
            verification="rerun smoke suite",
            reversible=True,
        ),
        scope_resolution=ScopeResolutionReport(
            report_id="SCOPE-1",
            strategy_id="STRAT-1",
            entries=[
                ScopeResolutionEntry(
                    path_or_symbol="src/service/lookup.py",
                    kind="file",
                    exists=True,
                )
            ],
            fully_resolved=True,
        ),
        evidence_ids=["EVID-1"],
        eligible=eligible,
        gate_reasons=(
            gate_reasons if gate_reasons is not None else ([] if eligible else ["blocked"])
        ),
    )


def _finding_set_kwargs() -> dict[str, Any]:
    return {
        "artifact_id": "ART-FS-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _now(),
        "producer": _producer(),
        "content_digest": _digest("b"),
        "evidence_bundle_digest": _digest("c"),
        "findings": [_finding()],
    }


def _solution_portfolio_kwargs(
    strategies: list[SolutionStrategy] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_id": "ART-SP-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _now(),
        "producer": _producer(),
        "content_digest": _digest("d"),
        "finding_set_digest": _digest("e"),
        "strategies": strategies if strategies is not None else [_solution_strategy(eligible=True)],
        "quality_report_digest": _digest("f"),
    }


def _quality_gate(passed: bool = True) -> QualityGateResult:
    return QualityGateResult(dimension="evidence_integrity", passed=passed)


def _a3_quality_report_kwargs(passed: bool = True) -> dict[str, Any]:
    return {
        "artifact_id": "ART-QR-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _now(),
        "producer": _producer(),
        "content_digest": _digest("0"),
        "finding_set_digest": _digest("1"),
        "solution_portfolio_digest": _digest("2"),
        "passed": passed,
        "portfolio_gate_results": [_quality_gate(passed)],
    }


def test_finding_set_construction_and_pinned_fields() -> None:
    finding_set = FindingSet(**_finding_set_kwargs())
    assert finding_set.artifact_type == "FindingSet"
    assert finding_set.schema_version == "1.0"


def test_finding_set_requires_findings() -> None:
    kwargs = _finding_set_kwargs()
    del kwargs["findings"]
    with pytest.raises(ValidationError, match="findings"):
        FindingSet(**kwargs)


def test_finding_set_rejects_malformed_digest() -> None:
    kwargs = _finding_set_kwargs()
    kwargs["evidence_bundle_digest"] = "not-a-digest"
    with pytest.raises(ValidationError, match="evidence_bundle_digest"):
        FindingSet(**kwargs)


def test_solution_portfolio_construction_and_pinned_fields() -> None:
    portfolio = SolutionPortfolio(**_solution_portfolio_kwargs())
    assert portfolio.artifact_type == "SolutionPortfolio"
    assert portfolio.schema_version == "1.0"


def test_solution_portfolio_requires_at_least_one_eligible_strategy() -> None:
    kwargs = _solution_portfolio_kwargs(
        strategies=[_solution_strategy(eligible=False, gate_reasons=["blocked"])]
    )
    with pytest.raises(ValidationError, match="at least one eligible strategy"):
        SolutionPortfolio(**kwargs)


def test_solution_portfolio_requires_strategies() -> None:
    kwargs = _solution_portfolio_kwargs()
    del kwargs["strategies"]
    with pytest.raises(ValidationError, match="strategies"):
        SolutionPortfolio(**kwargs)


def test_solution_portfolio_rejects_malformed_digest() -> None:
    kwargs = _solution_portfolio_kwargs()
    kwargs["finding_set_digest"] = "bad"
    with pytest.raises(ValidationError, match="finding_set_digest"):
        SolutionPortfolio(**kwargs)


def test_a3_quality_report_construction_and_pinned_fields() -> None:
    report = A3QualityReport(**_a3_quality_report_kwargs())
    assert report.artifact_type == "A3QualityReport"
    assert report.schema_version == "1.0"


def test_a3_quality_report_passed_must_match_gate_results() -> None:
    kwargs = _a3_quality_report_kwargs(passed=True)
    kwargs["portfolio_gate_results"] = [_quality_gate(passed=False)]
    with pytest.raises(ValidationError, match="aggregated gate results"):
        A3QualityReport(**kwargs)


def test_a3_quality_report_requires_portfolio_gate_results() -> None:
    kwargs = _a3_quality_report_kwargs()
    del kwargs["portfolio_gate_results"]
    with pytest.raises(ValidationError, match="portfolio_gate_results"):
        A3QualityReport(**kwargs)


def test_finding_verified_cause_requires_accepted_judgement() -> None:
    finding = _finding(claim_type="verified_cause", trust_level=TrustLevel.T4, verdict="accept")
    assert finding.claim_type == "verified_cause"
    with pytest.raises(ValidationError, match="accepted judge verdict"):
        _finding(claim_type="verified_cause", trust_level=TrustLevel.T4, verdict="reject")


def test_finding_verified_cause_requires_t4_trust() -> None:
    with pytest.raises(ValidationError, match="T4 trust level"):
        _finding(claim_type="verified_cause", trust_level=TrustLevel.T3, verdict="accept")


def test_citation_resolution_report_all_resolved_matches_entries() -> None:
    entry = CitationResolutionEntry(
        evidence_id="EVID-1", resolved=True, in_scope=True, supports_statement=True
    )
    report = CitationResolutionReport(
        report_id="CITE-1", finding_id="FIND-1", entries=[entry], all_resolved=True
    )
    assert report.all_resolved is True
    with pytest.raises(ValidationError, match="all_resolved must match"):
        CitationResolutionReport(
            report_id="CITE-1", finding_id="FIND-1", entries=[entry], all_resolved=False
        )


def test_scope_resolution_report_fully_resolved_matches_entries() -> None:
    entry = ScopeResolutionEntry(path_or_symbol="src/a.py", kind="file", exists=True)
    report = ScopeResolutionReport(
        report_id="SCOPE-1", strategy_id="STRAT-1", entries=[entry], fully_resolved=True
    )
    assert report.fully_resolved is True
    with pytest.raises(ValidationError, match="fully_resolved must match"):
        ScopeResolutionReport(
            report_id="SCOPE-1", strategy_id="STRAT-1", entries=[entry], fully_resolved=False
        )


def test_solution_strategy_ineligible_requires_gate_reasons() -> None:
    strategy = _solution_strategy(eligible=False, gate_reasons=["scope not fully resolved"])
    assert strategy.eligible is False
    with pytest.raises(ValidationError, match="gate reasons"):
        _solution_strategy(eligible=False, gate_reasons=[])


def test_analyzer_observation_source_discriminator() -> None:
    observation = AnalyzerObservation(
        observation_id="OBS-1",
        source="runtime",
        analyzer="pyroscope",
        analyzer_version="1.0",
        problem_signal_ids=["SIG-1"],
        description="hot symbol observed in profile",
        polarity="positive",
        coverage=0.5,
    )
    assert observation.source == "runtime"


def test_problem_signal_requires_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="evidence_ids"):
        ProblemSignal(
            signal_id="SIG-1",
            criterion_id="CRIT-1",
            metric_id="METRIC-1",
            signal_kind="regression",
            description="p95 latency regressed",
            baseline_value=100.0,
            target_value=80.0,
            unit="ms",
            evidence_ids=[],
            detected_at=_now(),
        )
