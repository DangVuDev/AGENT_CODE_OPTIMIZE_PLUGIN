from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope


class RegistryKind(StrEnum):
    REPOSITORY = "repository"
    FEATURE = "feature"
    OWNER = "owner"
    METRIC = "metric"
    WORKLOAD = "workload"
    QUERY = "query"
    COLLECTOR = "collector"
    ANALYZER = "analyzer"
    POLICY = "policy"
    MODEL_PROVIDER = "model_provider"


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class BoundedContextContract(ContractModel):
    context_id: str = Field(min_length=1)
    maximum_bytes: int = Field(gt=0)
    allowed_classifications: set[DataClassification] = Field(min_length=1)
    redaction_profile_id: str = Field(min_length=1)
    allowed_artifact_types: set[str] = Field(min_length=1)
    allow_raw_source: bool = False
    allow_secrets: Literal[False] = False


class RegistryRecord(ContractModel):
    """Versioned, tenant-scoped registry record with an immutable payload."""

    registry_kind: RegistryKind
    record_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    tenant_id: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    valid_from: datetime
    valid_until: datetime | None = None
    payload: dict[str, Any]
    classification: DataClassification = DataClassification.INTERNAL

    @model_validator(mode="after")
    def validate_validity_window(self) -> RegistryRecord:
        if self.valid_from.tzinfo is None:
            raise ValueError("registry valid_from must be timezone-aware")
        if self.valid_until is not None:
            if self.valid_until.tzinfo is None:
                raise ValueError("registry valid_until must be timezone-aware")
            if self.valid_until <= self.valid_from:
                raise ValueError("registry valid_until must be after valid_from")
        return self


class RepositoryRegistration(ContractModel):
    repository_id: str = Field(min_length=1)
    allowed_root_id: str = Field(min_length=1)
    canonical_path_ref: str = Field(min_length=1)
    default_revision: str | None = None
    excluded_paths: list[str] = Field(default_factory=list)


class FeatureRegistration(ContractModel):
    feature_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    service_names: list[str] = Field(default_factory=list)


class OwnerRegistration(ContractModel):
    owner_id: str = Field(min_length=1)
    feature_ids: list[str] = Field(default_factory=list)
    repository_ids: list[str] = Field(default_factory=list)
    roles: set[str] = Field(min_length=1)
    escalation_owner_id: str | None = None


class MetricRegistration(ContractModel):
    metric_id: str = Field(min_length=1)
    schema_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    unit: str = Field(min_length=1)
    value_type: Literal["counter", "gauge", "histogram", "summary", "score"]
    material_dimensions: set[str] = Field(default_factory=set)


class WorkloadRegistration(ContractModel):
    workload_id: str = Field(min_length=1)
    command_id: str | None = None
    dataset_id: str | None = None
    environment_id: str = Field(min_length=1)
    maximum_seconds: int = Field(gt=0)


class QueryRegistration(ContractModel):
    query_id: str = Field(min_length=1)
    collector_id: str = Field(min_length=1)
    query_template: str = Field(min_length=1)
    allowed_parameters: set[str] = Field(default_factory=set)
    maximum_window_seconds: int = Field(gt=0)
    redaction_profile_id: str = Field(min_length=1)


class CollectorRegistration(ContractModel):
    collector_id: str = Field(min_length=1)
    collector_type: Literal["metrics", "logs", "traces", "profiles", "llm", "command"]
    endpoint_ref: str | None = None
    secret_refs: list[str] = Field(default_factory=list)
    supported_metric_ids: set[str] = Field(default_factory=set)


class AnalyzerRegistration(ContractModel):
    analyzer_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    supported_languages: set[str] = Field(default_factory=set)
    network_access: bool = False


class PolicyRegistration(ContractModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    decision_types: set[str] = Field(min_length=1)
    implementation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ModelProviderRegistration(ContractModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    capabilities: set[str] = Field(min_length=1)
    maximum_context_tokens: int = Field(gt=0)
    data_disclosure_classifications: set[str] = Field(default_factory=set)


class RegistrySnapshot(ArtifactEnvelope):
    artifact_type: Literal["RegistrySnapshot"] = "RegistrySnapshot"
    schema_version: Literal["1.0"] = "1.0"
    records: list[RegistryRecord]
    snapshot_at: datetime

    @model_validator(mode="after")
    def validate_unique_record_versions(self) -> RegistrySnapshot:
        identities = [
            (record.registry_kind, record.record_id, record.version) for record in self.records
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("registry snapshot contains duplicate record versions")
        if self.snapshot_at.tzinfo is None:
            raise ValueError("registry snapshot timestamp must be timezone-aware")
        return self
