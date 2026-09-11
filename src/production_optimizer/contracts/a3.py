from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .a2 import TrustLevel
from .base import ContractModel
from .envelope import ArtifactEnvelope

# NOTE: the four analyzer-family outputs named in the A3 task catalogue
# (`AnalyzerObservation`, `SemanticObservation`, `DomainObservation`,
# `RuntimeCorrelation`) are consolidated into one `AnalyzerObservation` model
# with a `source` discriminator field, since they share an identical shape
# (files/symbols located near a signal, a polarity and analyzer coverage) and
# keeping them separate would only duplicate fields with no behavioral gain.


class ProblemSignal(ContractModel):
    signal_id: str = Field(min_length=1)
    criterion_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    signal_kind: Literal["threshold_breach", "regression", "hotspot", "cost_inflation"]
    description: str = Field(min_length=1, max_length=2000)
    baseline_value: float
    target_value: float
    unit: str = Field(min_length=1)
    effect_size: float | None = None
    evidence_ids: list[str] = Field(min_length=1)
    detected_at: datetime


class AnalyzerObservation(ContractModel):
    observation_id: str = Field(min_length=1)
    source: Literal["syntax", "semantic", "domain", "runtime"]
    analyzer: str = Field(min_length=1)
    analyzer_version: str = Field(min_length=1)
    problem_signal_ids: list[str] = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1, max_length=2000)
    polarity: Literal["positive", "negative"]
    coverage: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class CitationResolutionEntry(ContractModel):
    evidence_id: str = Field(min_length=1)
    resolved: bool
    in_scope: bool
    supports_statement: bool
    reason: str | None = None


class CitationResolutionReport(ContractModel):
    report_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    entries: list[CitationResolutionEntry] = Field(min_length=1)
    all_resolved: bool

    @model_validator(mode="after")
    def validate_all_resolved(self) -> CitationResolutionReport:
        computed = all(
            entry.resolved and entry.in_scope and entry.supports_statement for entry in self.entries
        )
        if self.all_resolved != computed:
            raise ValueError("all_resolved must match entry resolution results")
        return self


class FindingJudgement(ContractModel):
    judge_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    verdict: Literal["accept", "reject"]
    reasons: list[str] = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


class Finding(ContractModel):
    finding_id: str = Field(min_length=1)
    problem_signal_ids: list[str] = Field(min_length=1)
    claim_type: Literal["observation", "hypothesis", "verified_cause"]
    symptom: str = Field(min_length=1, max_length=2000)
    scope_files: list[str] = Field(default_factory=list)
    scope_symbols: list[str] = Field(default_factory=list)
    runtime_path: str | None = None
    causal_claim: str = Field(min_length=1, max_length=2000)
    supporting_evidence_ids: list[str] = Field(min_length=1)
    counterevidence_ids: list[str] = Field(default_factory=list)
    analyzer_coverage: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    trust_level: TrustLevel
    judgement: FindingJudgement
    unknowns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verified_cause_maturity(self) -> Finding:
        if self.claim_type == "verified_cause":
            if self.judgement.verdict != "accept":
                raise ValueError("a verified_cause finding requires an accepted judge verdict")
            if self.trust_level != TrustLevel.T4:
                raise ValueError("a verified_cause finding requires T4 trust level")
        return self


class CriterionImpact(ContractModel):
    criterion_id: str = Field(min_length=1)
    direction: Literal["improves", "worsens", "neutral", "unknown"]
    confidence: float = Field(ge=0, le=1)
    basis: Literal["measured", "forecast"]
    magnitude: float | None = None
    unit: str | None = None


class ImpactAssessment(ContractModel):
    assessment_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    criterion_impacts: list[CriterionImpact] = Field(min_length=1)
    guardrail_impacts: list[CriterionImpact] = Field(default_factory=list["CriterionImpact"])


class TradeoffAnalysis(ContractModel):
    analysis_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    pros: list[str] = Field(min_length=1)
    cons: list[str] = Field(min_length=1)
    prerequisites: list[str] = Field(default_factory=list)
    effort: Literal["low", "medium", "high"]
    uncertainty: float = Field(ge=0, le=1)
    operational_effects: list[str] = Field(default_factory=list)
    opportunity_cost: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ValidationPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    test_command_ids: list[str] = Field(default_factory=list)
    benchmark_protocol: str = Field(min_length=1)
    expected_metric_movements: dict[str, str] = Field(default_factory=dict)
    stop_conditions: list[str] = Field(min_length=1)


class RollbackPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    mechanism: str = Field(min_length=1, max_length=2000)
    command_id: str | None = None
    verification: str = Field(min_length=1)
    reversible: bool


class ScopeResolutionEntry(ContractModel):
    path_or_symbol: str = Field(min_length=1)
    kind: Literal["file", "symbol", "config_key", "prompt", "dependency"]
    exists: bool
    proposed_creation: bool = False


class ScopeResolutionReport(ContractModel):
    report_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    entries: list[ScopeResolutionEntry] = Field(min_length=1)
    fully_resolved: bool

    @model_validator(mode="after")
    def validate_fully_resolved(self) -> ScopeResolutionReport:
        computed = all(entry.exists or entry.proposed_creation for entry in self.entries)
        if self.fully_resolved != computed:
            raise ValueError("fully_resolved must match entry resolution state")
        return self


class RiskAssessment(ContractModel):
    assessment_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    risk_tier: Literal["experiment_config", "prompt", "code", "architecture"]
    blast_radius: str = Field(min_length=1)
    reversibility: Literal["instant", "fast", "slow", "irreversible"]
    uncertainty: float = Field(ge=0, le=1)
    migration_impact: bool
    security_impact: bool
    factors: list[str] = Field(default_factory=list)


class Treatment(ContractModel):
    variable: str = Field(min_length=1)
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)


class ExperimentPhaseTemplate(ContractModel):
    phase_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    phase_kind: Literal["diagnostic", "implementation"]
    treatment: Treatment
    risk_factors: list[str] = Field(default_factory=list)
    expected_observations: list[str] = Field(default_factory=list)


class SolutionStrategy(ContractModel):
    strategy_id: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    mechanism: str = Field(min_length=1, max_length=4000)
    strategy_tradeoffs: str = Field(min_length=1, max_length=2000)
    phase_templates: list[ExperimentPhaseTemplate] = Field(min_length=1)
    risk_ceiling: Literal["experiment_config", "prompt", "code", "architecture"]
    risk_assessment: RiskAssessment
    impact_assessment: ImpactAssessment
    tradeoff_analysis: TradeoffAnalysis
    validation_plan: ValidationPlan
    rollback_plan: RollbackPlan
    scope_resolution: ScopeResolutionReport
    evidence_ids: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    eligible: bool
    gate_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ineligible_has_reasons(self) -> SolutionStrategy:
        if not self.eligible and not self.gate_reasons:
            raise ValueError("an ineligible strategy must include gate reasons")
        return self


class FindingSet(ArtifactEnvelope):
    artifact_type: Literal["FindingSet"] = "FindingSet"
    schema_version: Literal["1.0"] = "1.0"
    evidence_bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    findings: list[Finding] = Field(min_length=1)
    observations: list[AnalyzerObservation] = Field(default_factory=list["AnalyzerObservation"])
    citation_reports: list[CitationResolutionReport] = Field(
        default_factory=list["CitationResolutionReport"]
    )
    coverage_gaps: list[str] = Field(default_factory=list)


class SolutionPortfolio(ArtifactEnvelope):
    artifact_type: Literal["SolutionPortfolio"] = "SolutionPortfolio"
    schema_version: Literal["1.0"] = "1.0"
    finding_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategies: list[SolutionStrategy] = Field(min_length=1)
    quality_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_at_least_one_eligible(self) -> SolutionPortfolio:
        if not any(strategy.eligible for strategy in self.strategies):
            raise ValueError("a sealed solution portfolio requires at least one eligible strategy")
        return self


class QualityGateResult(ContractModel):
    dimension: str = Field(min_length=1)
    passed: bool
    detail: str | None = None


class A3QualityReport(ArtifactEnvelope):
    artifact_type: Literal["A3QualityReport"] = "A3QualityReport"
    schema_version: Literal["1.0"] = "1.0"
    finding_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    solution_portfolio_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    passed: bool
    finding_gate_results: list[QualityGateResult] = Field(default_factory=list["QualityGateResult"])
    strategy_gate_results: list[QualityGateResult] = Field(
        default_factory=list["QualityGateResult"]
    )
    cause_maturity_gate_results: list[QualityGateResult] = Field(
        default_factory=list["QualityGateResult"]
    )
    portfolio_gate_results: list[QualityGateResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_passed_matches_gates(self) -> A3QualityReport:
        all_results = (
            self.finding_gate_results
            + self.strategy_gate_results
            + self.cause_maturity_gate_results
            + self.portfolio_gate_results
        )
        computed = all(result.passed for result in all_results)
        if self.passed != computed:
            raise ValueError("passed must match the aggregated gate results")
        return self
