from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from .artifacts import ArtifactRef
from .base import ContractModel
from .envelope import ArtifactEnvelope


class TrustLevel(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"


class FileIdentity(ContractModel):
    relative_path: str = Field(min_length=1)
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    executable: bool = False


class SourceSnapshot(ArtifactEnvelope):
    artifact_type: Literal["SourceSnapshot"] = "SourceSnapshot"
    schema_version: Literal["1.0"] = "1.0"
    repository_id: str
    canonical_path_ref: str
    git_revision: str | None = None
    dirty: bool
    files: list[FileIdentity]
    submodule_revisions: dict[str, str] = Field(default_factory=dict)
    exclusions: list[str] = Field(default_factory=list)


class RepositoryCommand(ContractModel):
    command_id: str
    argv: list[str] = Field(min_length=1)
    working_directory: str
    kind: Literal["build", "unit", "integration", "benchmark", "lint", "type", "security"]
    # Where A2.31 got this command from -- never silently blurred, since
    # "llm_suggested" carries materially different trust: it always halts
    # for human approval before A2.50 may authorize it (see
    # `docs/adr/0002-model-provider-port.md`-style human-in-the-loop gate);
    # "pyproject_toml"/"ci_config" are both real, detected-not-guessed
    # commands and never require approval.
    source: Literal["pyproject_toml", "ci_config", "llm_suggested"]


class RepositoryManifest(ArtifactEnvelope):
    artifact_type: Literal["RepositoryManifest"] = "RepositoryManifest"
    schema_version: Literal["1.0"] = "1.0"
    languages: dict[str, float]
    modules: list[str]
    symbols_ref: ArtifactRef | None = None
    manifest_files: list[str]
    test_roots: list[str]
    commands: list[RepositoryCommand]
    tool_coverage: dict[str, float]
    exclusions: list[str] = Field(default_factory=list)


class EvidenceIdentity(ContractModel):
    feature_id: str
    repository_id: str
    source_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workload_id: str
    dataset_id: str | None = None
    environment_id: str
    model_id: str | None = None
    prompt_version: str | None = None
    hardware_profile: str
    concurrency: int = Field(ge=1)
    cache_state: str
    metric_schema_version: str
    sample_id: str
    observed_at: datetime
    trace_id: str | None = None
    collector: str
    collector_version: str


class EvidenceItem(ContractModel):
    evidence_id: str
    evidence_type: str
    trust_level: TrustLevel
    identity: EvidenceIdentity
    raw_ref: ArtifactRef
    normalized_ref: ArtifactRef | None = None
    value: float | str | bool | None = None
    unit: str | None = None


class MetricAggregate(ContractModel):
    metric_id: str
    unit: str
    sample_ids: list[str] = Field(min_length=1)
    count: int = Field(ge=1)
    minimum: float
    maximum: float
    mean: float
    percentile_50: float | None = None
    percentile_95: float | None = None


class BaselineSnapshot(ArtifactEnvelope):
    artifact_type: Literal["BaselineSnapshot"] = "BaselineSnapshot"
    schema_version: Literal["1.0"] = "1.0"
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workload_id: str
    environment_id: str
    window_start: datetime
    window_end: datetime
    aggregates: list[MetricAggregate]


class EvidenceBundle(ArtifactEnvelope):
    artifact_type: Literal["EvidenceBundle"] = "EvidenceBundle"
    schema_version: Literal["1.0"] = "1.0"
    baseline_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence: list[EvidenceItem] = Field(min_length=1)
    collector_versions: dict[str, str]
    coverage: dict[str, float]


class EvidenceQualityReport(ArtifactEnvelope):
    artifact_type: Literal["EvidenceQualityReport"] = "EvidenceQualityReport"
    schema_version: Literal["1.0"] = "1.0"
    passed: bool
    mandatory_coverage: dict[str, bool]
    sample_failures: list[str]
    freshness_failures: list[str]
    integrity_failures: list[str]
    redaction_failures: list[str]
    collector_failures: dict[str, str]


class DimensionVerdict(ContractModel):
    dimension: str
    comparable: bool
    baseline_value: str
    expected_value: str
    material: bool
    reason: str


class ComparabilityReport(ArtifactEnvelope):
    artifact_type: Literal["ComparabilityReport"] = "ComparabilityReport"
    schema_version: Literal["1.0"] = "1.0"
    comparable: bool
    dimensions: list[DimensionVerdict]
    policy_version: str


class A2IntakeDecision(ArtifactEnvelope):
    artifact_type: Literal["A2IntakeDecision"] = "A2IntakeDecision"
    schema_version: Literal["1.0"] = "1.0"
    verified: bool
    mismatches: list[str] = Field(default_factory=list)
    source_reachable: bool
    policy_version: str = Field(min_length=1)


def _empty_commands() -> list[RepositoryCommand]:
    return []


class VerificationManifest(ArtifactEnvelope):
    """A2.31 output: only repository-owned commands the snapshot can justify.

    Distinct from `RepositoryManifest.commands` (A2.30), which stays empty
    until this node resolves argv against real, detected tool configuration.
    """

    artifact_type: Literal["VerificationManifest"] = "VerificationManifest"
    schema_version: Literal["1.0"] = "1.0"
    commands: list[RepositoryCommand] = Field(default_factory=_empty_commands)
    rejected_commands: list[str] = Field(default_factory=list)


class CollectorBinding(ContractModel):
    requirement_id: str = Field(min_length=1)
    collector_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    window_seconds: int = Field(gt=0)
    aggregation: str = Field(min_length=1)
    minimum_samples: int = Field(ge=1)


def _empty_bindings() -> list[CollectorBinding]:
    return []


class CollectorPlan(ArtifactEnvelope):
    artifact_type: Literal["CollectorPlan"] = "CollectorPlan"
    schema_version: Literal["1.0"] = "1.0"
    bindings: list[CollectorBinding] = Field(default_factory=_empty_bindings)
    unresolved_requirements: list[str] = Field(default_factory=list)
    catalog_version: str = Field(min_length=1)


class EnvironmentManifest(ArtifactEnvelope):
    artifact_type: Literal["EnvironmentManifest"] = "EnvironmentManifest"
    schema_version: Literal["1.0"] = "1.0"
    tool_versions: dict[str, str] = Field(default_factory=dict)
    hardware_profile: str = Field(min_length=1)
    concurrency: int = Field(ge=1)
    cache_state: str = Field(min_length=1)


class ExecutionAuthorization(ArtifactEnvelope):
    """A2.50 output.

    Not produced by any registered handler yet: authorizing argv, write
    roots, egress, secret leases and worker jobs requires `PolicyPort`,
    `SecretsBroker` and `WorkerBroker` wired into `NodePorts`, none of which
    exist there today. The contract is defined ahead of the port work so the
    fan-out nodes (A2.60-A2.64) that depend on it have a stable target shape.
    """

    artifact_type: Literal["ExecutionAuthorization"] = "ExecutionAuthorization"
    schema_version: Literal["1.0"] = "1.0"
    authorized: bool
    denied_capabilities: list[str] = Field(default_factory=list)
    allowed_write_roots: list[str] = Field(default_factory=list)
    allowed_egress: list[str] = Field(default_factory=list)
    worker_job_ids: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)


def _empty_evidence_items() -> list[EvidenceItem]:
    return []


class BranchEvidenceRefs(ArtifactEnvelope):
    """Shared fan-out branch container for A2.60-A2.64.

    Each branch (static/test/metric/telemetry/source_map) owns a separate
    intent and artifact set per the Lane 1 playbook fan-out rules, but they
    share one schema shape since none of them can be populated with real
    evidence without a collector/analyzer/worker port that does not exist
    yet (see `ExecutionAuthorization`).
    """

    artifact_type: Literal["BranchEvidenceRefs"] = "BranchEvidenceRefs"
    schema_version: Literal["1.0"] = "1.0"
    branch_id: str = Field(pattern=r"^A2\.6[0-4]$")
    branch_kind: Literal["static", "test", "metric", "telemetry", "source_map"]
    evidence: list[EvidenceItem] = Field(default_factory=_empty_evidence_items)
    coverage: dict[str, float] = Field(default_factory=dict)
    unavailable_reason: str | None = None


class RawEvidenceFanIn(ArtifactEnvelope):
    artifact_type: Literal["RawEvidenceFanIn"] = "RawEvidenceFanIn"
    schema_version: Literal["1.0"] = "1.0"
    branch_status: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class NormalizedEvidenceSet(ArtifactEnvelope):
    artifact_type: Literal["NormalizedEvidenceSet"] = "NormalizedEvidenceSet"
    schema_version: Literal["1.0"] = "1.0"
    evidence: list[EvidenceItem] = Field(default_factory=_empty_evidence_items)
    conversion_failures: list[str] = Field(default_factory=list)
