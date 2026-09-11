from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope

# NOTE: trust levels are duplicated here as an inline Literal rather than
# importing `TrustLevel` from `a2.py`, to avoid a cross-lane import: only
# `a3.py` is permitted to import from `a2.py` per the platform wiring plan.
TrustLevelLiteral = Literal["T0", "T1", "T2", "T3", "T4"]

# NOTE: the four detection-family outputs named in the B1 task catalogue
# (`ThresholdSignal`, `RegressionSignal`, `HotspotSignal`,
# `LLMOpportunitySignal`) are consolidated into one `DetectionSignal` model
# with a `signal_kind` discriminator, mirroring the same consolidation
# judgment applied to A3's analyzer-observation family: the four variants
# share an identical shape (a metric, an observed/baseline value, severity
# and the run groups it was detected in) and gain nothing from separate
# classes.


class RunGroup(ContractModel):
    group_id: str = Field(min_length=1)
    feature_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    dataset_id: str | None = None
    environment_id: str = Field(min_length=1)
    model_id: str | None = None
    sample_ids: list[str] = Field(min_length=1)
    window_start: datetime
    window_end: datetime
    trust_level: TrustLevelLiteral

    @model_validator(mode="after")
    def validate_window(self) -> RunGroup:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class DetectionSignal(ContractModel):
    signal_id: str = Field(min_length=1)
    signal_kind: Literal["threshold", "regression", "hotspot", "llm_quality"]
    run_group_ids: list[str] = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=2000)
    baseline_value: float | None = None
    observed_value: float
    effect_size: float | None = None
    unit: str = Field(min_length=1)
    severity: float = Field(ge=0, le=1)
    detected_at: datetime


class FeatureBinding(ContractModel):
    binding_id: str = Field(min_length=1)
    signal_ids: list[str] = Field(min_length=1)
    feature_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    method: Literal["explicit_label", "trace_mapping", "service_mapping", "symbol_mapping"]
    alternatives: list[str] = Field(default_factory=list)
    resolved: bool

    @model_validator(mode="after")
    def validate_resolved_requires_feature(self) -> FeatureBinding:
        if self.resolved and self.feature_id is None:
            raise ValueError("a resolved feature binding must include a feature_id")
        return self


class SourceBinding(ContractModel):
    binding_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    git_revision: str | None = None
    resolved: bool
    unresolved_reason: str | None = None

    @model_validator(mode="after")
    def validate_unresolved_reason(self) -> SourceBinding:
        if not self.resolved and not self.unresolved_reason:
            raise ValueError("an unresolved source binding requires unresolved_reason")
        return self


class OwnershipBinding(ContractModel):
    binding_id: str = Field(min_length=1)
    code_owner: str | None = None
    service_owner: str | None = None
    decision_owner: str | None = None
    conflicts: list[str] = Field(default_factory=list)
    resolved: bool


class OpportunityScore(ContractModel):
    score_id: str = Field(min_length=1)
    severity: float = Field(ge=0, le=1)
    frequency: float = Field(ge=0, le=1)
    business_impact: float = Field(ge=0, le=1)
    evidence_trust: float = Field(ge=0, le=1)
    addressability: float = Field(ge=0, le=1)
    strategic_priority: float = Field(ge=0, le=1)
    composite: float = Field(ge=0, le=1)
    policy_version: str = Field(min_length=1)


class QualificationDecision(ContractModel):
    decision_id: str = Field(min_length=1)
    qualified: bool
    reasons: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reasons_when_disqualified(self) -> QualificationDecision:
        if not self.qualified and not self.reasons:
            raise ValueError("a disqualified opportunity must include reasons")
        return self


class CooldownDecision(ContractModel):
    decision_id: str = Field(min_length=1)
    suppressed: bool
    reason: str | None = None
    cooldown_expires_at: datetime | None = None
    override_applied: bool = False

    @model_validator(mode="after")
    def validate_suppressed_reason(self) -> CooldownDecision:
        if self.suppressed and not self.reason:
            raise ValueError("a suppressed candidate requires a reason")
        return self


class DetectionReport(ArtifactEnvelope):
    artifact_type: Literal["DetectionReport"] = "DetectionReport"
    schema_version: Literal["1.0"] = "1.0"
    scan_id: str = Field(min_length=1)
    window_start: datetime
    window_end: datetime
    run_groups: list[RunGroup] = Field(default_factory=list["RunGroup"])
    signals: list[DetectionSignal] = Field(default_factory=list["DetectionSignal"])
    exclusions: list[str] = Field(default_factory=list)
    feature_bindings: list[FeatureBinding] = Field(default_factory=list["FeatureBinding"])
    source_bindings: list[SourceBinding] = Field(default_factory=list["SourceBinding"])
    ownership_bindings: list[OwnershipBinding] = Field(default_factory=list["OwnershipBinding"])
    scores: list[OpportunityScore] = Field(default_factory=list["OpportunityScore"])
    qualification_decisions: list[QualificationDecision] = Field(
        default_factory=list["QualificationDecision"]
    )
    cooldown_decisions: list[CooldownDecision] = Field(default_factory=list["CooldownDecision"])

    @model_validator(mode="after")
    def validate_window(self) -> DetectionReport:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class QualifiedOpportunity(ArtifactEnvelope):
    """Envelope: detection report + owner/source binding + OptimizationRequest + A2 artifacts.

    Constituent artifacts are referenced by content digest rather than
    embedded, following the same linkage convention as `BaselineSnapshot`
    and `EvidenceBundle` in `a2.py`.
    """

    artifact_type: Literal["QualifiedOpportunity"] = "QualifiedOpportunity"
    schema_version: Literal["1.0"] = "1.0"
    detection_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    baseline_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence_bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    comparability_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_binding: SourceBinding
    owner_binding: OwnershipBinding
    case_start_id: str = Field(min_length=1)
