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
