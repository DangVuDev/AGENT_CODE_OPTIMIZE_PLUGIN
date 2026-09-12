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


class A3IntakeDecision(ArtifactEnvelope):
    artifact_type: Literal["A3IntakeDecision"] = "A3IntakeDecision"
    schema_version: Literal["1.0"] = "1.0"
    verified: bool
    mismatches: list[str] = Field(default_factory=list)
    gates_passed: bool


class EvidenceCatalogEntry(ContractModel):
    evidence_id: str = Field(min_length=1)
    metric_id: str | None = None
    trust_level: TrustLevel
    observed_at: datetime


def _empty_catalog_entries() -> list[EvidenceCatalogEntry]:
    return []


class EvidenceCatalog(ArtifactEnvelope):
    """A3.11 output: a lookup index over A2's `EvidenceBundle`.

    `by_metric` is a lookup authority only — presence here is not itself
    causal proof of anything, per the playbook ("Index is lookup authority,
    not causal proof").
    """

    artifact_type: Literal["EvidenceCatalog"] = "EvidenceCatalog"
    schema_version: Literal["1.0"] = "1.0"
    entries: list[EvidenceCatalogEntry] = Field(default_factory=_empty_catalog_entries)
    by_metric: dict[str, list[str]] = Field(default_factory=dict)


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


def _empty_problem_signals() -> list[ProblemSignal]:
    return []


class ProblemSignalSet(ArtifactEnvelope):
    """A3.20 output: every measured problem, before priority scoring.

    An empty list is a valid, honest outcome ("No measured problem ->
    close NO_ACTIONABLE_PROBLEM" per the playbook), not an error.
    """

    artifact_type: Literal["ProblemSignalSet"] = "ProblemSignalSet"
    schema_version: Literal["1.0"] = "1.0"
    signals: list[ProblemSignal] = Field(default_factory=_empty_problem_signals)


class PrioritizedSignalSet(ArtifactEnvelope):
    """A3.21 output: the same signals, ranked — nothing is dropped.

    Distinct `artifact_type` from `ProblemSignalSet` (not a re-seal of it)
    so the two coexist in `state["artifact_refs"]` without an artifact_id
    collision.
    """

    artifact_type: Literal["PrioritizedSignalSet"] = "PrioritizedSignalSet"
    schema_version: Literal["1.0"] = "1.0"
    signals: list[ProblemSignal] = Field(default_factory=_empty_problem_signals)
    priority_scores: dict[str, float] = Field(default_factory=dict)


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


def _empty_observations() -> list[AnalyzerObservation]:
    return []


class AnalyzerObservationBranch(ArtifactEnvelope):
    """Shared fan-out shape for A3.30-A3.33 — same pattern as A2's
    `BranchEvidenceRefs`: one schema, `branch_id`/`source` differentiate.

    `unavailable_reason` set + empty `observations` is a legitimate result
    (e.g. A3.32 with no registered domain analyzer, A3.33 with no telemetry
    evidence to correlate), not a stub.
    """

    artifact_type: Literal["AnalyzerObservationBranch"] = "AnalyzerObservationBranch"
    schema_version: Literal["1.0"] = "1.0"
    branch_id: str = Field(pattern=r"^A3\.3[0-3]$")
    source: Literal["syntax", "semantic", "domain", "runtime"]
    observations: list[AnalyzerObservation] = Field(default_factory=_empty_observations)
    coverage_gaps: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None


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


def _empty_citation_reports() -> list[CitationResolutionReport]:
    return []


class CitationResolutionReportSet(ArtifactEnvelope):
    artifact_type: Literal["CitationResolutionReportSet"] = "CitationResolutionReportSet"
    schema_version: Literal["1.0"] = "1.0"
    reports: list[CitationResolutionReport] = Field(default_factory=_empty_citation_reports)


class FindingJudgement(ContractModel):
    judge_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    verdict: Literal["accept", "reject"]
    reasons: list[str] = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


def _empty_judgements() -> list[FindingJudgement]:
    return []


class FindingJudgementSet(ArtifactEnvelope):
    artifact_type: Literal["FindingJudgementSet"] = "FindingJudgementSet"
    schema_version: Literal["1.0"] = "1.0"
    judgements: list[FindingJudgement] = Field(default_factory=_empty_judgements)


class FindingDraft(ContractModel):
    """A3.40 generator output: everything `Finding` needs except `trust_level`
    and `judgement` — those are assigned downstream (A3.50 judge, A3.51
    maturity) and a generator must not self-assign them.
    """

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
    unknowns: list[str] = Field(default_factory=list)


def _empty_finding_drafts() -> list[FindingDraft]:
    return []


class FindingDraftSet(ArtifactEnvelope):
    artifact_type: Literal["FindingDraftSet"] = "FindingDraftSet"
    schema_version: Literal["1.0"] = "1.0"
    drafts: list[FindingDraft] = Field(default_factory=_empty_finding_drafts)
    generation_failures: list[str] = Field(default_factory=list)


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


class StrategyDraft(ContractModel):
    """A strategy under construction between A3.60 and A3.80.

    Mirrors `SolutionStrategy` but with every field only A3.70/A3.80 (scope,
    risk) or A3.62-A3.64 (impact, tradeoff, validation, rollback) can supply
    left optional — a generator at A3.60 genuinely cannot know them yet.
    Once every optional field is filled (by A3.80), the draft is converted
    to a real `SolutionStrategy` — see `SolutionStrategySet`.
    """

    strategy_id: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    mechanism: str = Field(min_length=1, max_length=4000)
    strategy_tradeoffs: str = Field(min_length=1, max_length=2000)
    phase_templates: list[ExperimentPhaseTemplate] = Field(min_length=1)
    risk_ceiling: Literal["experiment_config", "prompt", "code", "architecture"]
    evidence_ids: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    target_paths: list[str] = Field(default_factory=list)
    impact_assessment: ImpactAssessment | None = None
    tradeoff_analysis: TradeoffAnalysis | None = None
    validation_plan: ValidationPlan | None = None
    rollback_plan: RollbackPlan | None = None
    scope_resolution: ScopeResolutionReport | None = None
    risk_assessment: RiskAssessment | None = None
    eligible: bool = True
    gate_reasons: list[str] = Field(default_factory=list)


def _empty_strategy_drafts() -> list[StrategyDraft]:
    return []


class StrategyDraftSet(ArtifactEnvelope):
    """Carries strategy drafts through A3.60-A3.70.

    Reused, unmodified, by each stage in that linear chain — each node
    reads the previous stage's ref by producer node id (never by bare
    `artifact_type`, which would be ambiguous once more than one stage has
    written this same type) and writes its own, following the same
    node-scoped `artifact_id` technique `application/a2_handlers.py` uses
    for `BranchEvidenceRefs` (`_branch_envelope`/`_require_branch_ref`).
    """

    artifact_type: Literal["StrategyDraftSet"] = "StrategyDraftSet"
    schema_version: Literal["1.0"] = "1.0"
    strategies: list[StrategyDraft] = Field(default_factory=_empty_strategy_drafts)


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


def _empty_solution_strategies() -> list[SolutionStrategy]:
    return []


class SolutionStrategySet(ArtifactEnvelope):
    """A3.80 output: fully-assembled strategies, before the A3.81 quality gate.

    Distinct from `SolutionPortfolio` (A3.90's sealed handoff) because
    `SolutionPortfolio` requires `quality_report_digest`, which does not
    exist until A3.81 runs.
    """

    artifact_type: Literal["SolutionStrategySet"] = "SolutionStrategySet"
    schema_version: Literal["1.0"] = "1.0"
    strategies: list[SolutionStrategy] = Field(default_factory=_empty_solution_strategies)


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


class RevisionDirective(ArtifactEnvelope):
    """A3.82 output: which findings/strategies get regenerated, and why.

    `SolutionStrategy.gate_reasons`/`Finding` maturity failures already say
    *what* was wrong; this records the *decision* A3.82 made about it
    (provenance for the revision loop, per the "Must write artifact store"
    matrix). `targeted_*_ids` are the only items A3.60 may regenerate on the
    next pass — everything else is carried forward unchanged, per the
    playbook ("A3.82 may target only rejected findings/strategies; accepted
    artifacts are reused").
    """

    artifact_type: Literal["RevisionDirective"] = "RevisionDirective"
    schema_version: Literal["1.0"] = "1.0"
    attempt_number: int = Field(ge=1)
    targeted_finding_ids: list[str] = Field(default_factory=list)
    targeted_strategy_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
