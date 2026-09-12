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


class DiscoveryScanContext(ArtifactEnvelope):
    """B1.10's own sealed output: the scan window/trigger for this run.

    Minimal by design -- everything B1.20-40 discovers gets folded into
    `DetectionReport` (already the wide, incrementally-built envelope this
    lane converges on); this only needs to exist so B1.10 has something real
    to seal and every later node has a stable `scan_id` to reference.
    """

    artifact_type: Literal["DiscoveryScanContext"] = "DiscoveryScanContext"
    schema_version: Literal["1.0"] = "1.0"
    scan_id: str = Field(min_length=1)
    window_start: datetime
    window_end: datetime
    trigger: Literal["scheduled", "manual", "event"]

    @model_validator(mode="after")
    def validate_window(self) -> DiscoveryScanContext:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class RegisteredSource(ContractModel):
    source_id: str = Field(min_length=1)
    feature_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    local_path: str = Field(min_length=1)


class RegisteredSourceSet(ArtifactEnvelope):
    """B1.20's output: which feature/repository pairs this scan may consider.

    A real, tenant-scoped allowlist -- B1 must never open an opportunity for
    a repository/feature nobody registered for automatic discovery.
    """

    artifact_type: Literal["RegisteredSourceSet"] = "RegisteredSourceSet"
    schema_version: Literal["1.0"] = "1.0"
    sources: list[RegisteredSource] = Field(default_factory=list["RegisteredSource"])


class ObservedSourceIdentity(ArtifactEnvelope):
    """B1.21's output: a real fingerprint of the source as it exists right now."""

    artifact_type: Literal["ObservedSourceIdentity"] = "ObservedSourceIdentity"
    schema_version: Literal["1.0"] = "1.0"
    source_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    git_revision: str | None = None
    content_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class HistoricalRevision(ContractModel):
    revision_id: str = Field(min_length=1)
    observed_at: datetime


class HistoricalSourceInventory(ArtifactEnvelope):
    """B1.30's output: real prior revisions available to compare against."""

    artifact_type: Literal["HistoricalSourceInventory"] = "HistoricalSourceInventory"
    schema_version: Literal["1.0"] = "1.0"
    source_id: str = Field(min_length=1)
    revisions: list[HistoricalRevision] = Field(default_factory=list["HistoricalRevision"])


class ReadAuthorization(ArtifactEnvelope):
    """B1.31's output: whether this scan may query historical telemetry at all.

    Fail-closed by construction -- `allowed=False` requires `reasons`, same
    pattern as `QualificationDecision`/`CooldownDecision` below.
    """

    artifact_type: Literal["ReadAuthorization"] = "ReadAuthorization"
    schema_version: Literal["1.0"] = "1.0"
    scan_id: str = Field(min_length=1)
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    authorized_query_kinds: list[Literal["metrics", "logs", "traces", "llm_evidence"]] = Field(
        default_factory=list["Literal['metrics', 'logs', 'traces', 'llm_evidence']"]
    )

    @model_validator(mode="after")
    def validate_denied_reasons(self) -> ReadAuthorization:
        if not self.allowed and not self.reasons:
            raise ValueError("a denied read authorization must include reasons")
        return self


class HistoricalEvidenceItem(ContractModel):
    """One real historical observation -- shaped like `a2.EvidenceItem` but
    defined here rather than imported, since only `a3.py` may import from
    `a2.py` per the platform wiring plan (see the module-level NOTE above)."""

    evidence_id: str = Field(min_length=1)
    feature_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    value: float | None = None
    unit: str | None = None
    observed_at: datetime


class HistoricalEvidenceBranch(ArtifactEnvelope):
    """B1.32-35's own output -- deliberately not `a2.BranchEvidenceRefs`
    (its `branch_id` is pattern-locked to A2's own node ids)."""

    artifact_type: Literal["HistoricalEvidenceBranch"] = "HistoricalEvidenceBranch"
    schema_version: Literal["1.0"] = "1.0"
    branch_id: Literal["B1.32", "B1.33", "B1.34", "B1.35"]
    branch_kind: Literal["metrics", "logs", "traces", "llm_evidence"]
    evidence: list[HistoricalEvidenceItem] = Field(default_factory=list["HistoricalEvidenceItem"])
    unavailable_reason: str | None = None


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
