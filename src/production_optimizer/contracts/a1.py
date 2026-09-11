from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .base import ContractModel
from .envelope import ArtifactEnvelope


class Origin(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class ScopeProfile(StrEnum):
    LOCAL_SANDBOX = "local_sandbox"
    CONNECTED_PRODUCTION = "connected_production"


class ManualCasePayload(ContractModel):
    """Raw command payload read by A1.10 from object storage.

    This model is intentionally not graph state. `StartWorkflowCommand` carries
    only an `ArtifactRef`; A1.10 reads this payload through the artifact port,
    validates it, and stores compact derived artifacts back to state.
    """

    raw_text: str | None = Field(default=None, max_length=20000)
    structured_request: dict[str, Any] | None = None
    local_path: str = Field(min_length=1, max_length=2048)
    allowed_root: str = Field(min_length=1, max_length=2048)
    actor_id: str = Field(min_length=1, max_length=255)
    actor_role: str = Field(default="requester", min_length=1, max_length=100)
    policy_version: str = Field(default="intake-policy-v1", min_length=1, max_length=100)


class IntakeEnvelope(ArtifactEnvelope):
    artifact_type: Literal["IntakeEnvelope"] = "IntakeEnvelope"
    schema_version: Literal["1.0"] = "1.0"
    input_mode: Literal["raw", "structured", "mixed"]
    actor_id: str = Field(min_length=1, max_length=255)
    actor_role: str = Field(min_length=1, max_length=100)
    local_path: str = Field(min_length=1, max_length=2048)
    allowed_root: str = Field(min_length=1, max_length=2048)
    original_payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RawRequestDraft(ArtifactEnvelope):
    artifact_type: Literal["RawRequestDraft"] = "RawRequestDraft"
    schema_version: Literal["1.0"] = "1.0"
    origin: Origin
    objective_statement: str | None = Field(default=None, max_length=4000)
    feature_id: str | None = Field(default=None, max_length=255)
    metric_id: str | None = Field(default=None, max_length=255)
    direction: Literal["minimize", "maximize", "target"] | None = None
    target: float | None = None
    unit: str | None = Field(default=None, max_length=100)
    guardrail_metric_id: str | None = Field(default=None, max_length=255)
    workload_id: str | None = Field(default=None, max_length=255)
    dataset_id: str | None = Field(default=None, max_length=255)
    environment_id: str | None = Field(default=None, max_length=255)
    command_id: str | None = Field(default=None, max_length=255)
    extraction_confidence: float = Field(ge=0, le=1)
    unresolved_fields: list[str] = Field(default_factory=list)
    requester_hypothesis: str | None = Field(default=None, max_length=2000)


class LocalSourceIdentity(ArtifactEnvelope):
    artifact_type: Literal["LocalSourceIdentity"] = "LocalSourceIdentity"
    schema_version: Literal["1.0"] = "1.0"
    repository_id: str = Field(min_length=1)
    allowed_root_id: str = Field(min_length=1)
    canonical_path: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    git_revision: str | None = None
    dirty: bool
    untracked_count: int = Field(ge=0)
    submodule_revisions: dict[str, str] = Field(default_factory=dict)


class ProjectProfile(ArtifactEnvelope):
    artifact_type: Literal["ProjectProfile"] = "ProjectProfile"
    schema_version: Literal["1.0"] = "1.0"
    languages: dict[str, float] = Field(default_factory=dict)
    manifest_files: list[str] = Field(default_factory=list)
    test_roots: list[str] = Field(default_factory=list)
    generated_or_vendor_paths: list[str] = Field(default_factory=list)
    file_count: int = Field(ge=0)
    discovery_truncated: bool = False


class FeatureScope(ArtifactEnvelope):
    artifact_type: Literal["FeatureScope"] = "FeatureScope"
    schema_version: Literal["1.0"] = "1.0"
    feature_id: str = Field(min_length=1)
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=2000)


class A1QualityReport(ArtifactEnvelope):
    artifact_type: Literal["A1QualityReport"] = "A1QualityReport"
    schema_version: Literal["1.0"] = "1.0"
    passed: bool
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)


class SourceReference(ContractModel):
    repository_id: str = Field(min_length=1)
    allowed_root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    requested_revision: str | None = None


class Objective(ContractModel):
    statement: str = Field(min_length=1, max_length=4000)
    feature_id: str = Field(min_length=1)


class Criterion(ContractModel):
    criterion_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    direction: Literal["minimize", "maximize", "target"]
    target: float
    unit: str = Field(min_length=1)
    weight: float = Field(gt=0)


class Guardrail(ContractModel):
    guardrail_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    operator: Literal["lt", "lte", "eq", "gte", "gt"]
    threshold: float
    unit: str = Field(min_length=1)


class WorkloadContract(ContractModel):
    workload_id: str = Field(min_length=1)
    dataset_id: str | None = None
    environment_id: str = Field(min_length=1)
    command_id: str | None = None
    repetitions: int = Field(ge=1)
    warmup_runs: int = Field(ge=0)
    concurrency: int = Field(ge=1)
    cache_state: str = Field(min_length=1)


class EvidenceRequirement(ContractModel):
    requirement_id: str = Field(min_length=1)
    criterion_id: str
    accepted_source_types: set[str] = Field(min_length=1)
    minimum_samples: int = Field(ge=1)
    mandatory: bool = True


class ExecutionBudget(ContractModel):
    deadline_seconds: int = Field(gt=0)
    maximum_worker_seconds: int = Field(ge=0)
    maximum_model_tokens: int = Field(ge=0)
    maximum_storage_bytes: int = Field(ge=0)
    allowed_analyzers: set[str] = Field(default_factory=set)


class ApprovalBinding(ContractModel):
    approval_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1)


def _empty_criteria() -> list[Criterion]:
    return []


def _empty_guardrails() -> list[Guardrail]:
    return []


def _empty_evidence_requirements() -> list[EvidenceRequirement]:
    return []


class CanonicalObjective(ArtifactEnvelope):
    artifact_type: Literal["CanonicalObjective"] = "CanonicalObjective"
    schema_version: Literal["1.0"] = "1.0"
    objective: Objective | None = None
    requester_hypothesis: str | None = Field(default=None, max_length=2000)
    missing_fields: list[str] = Field(default_factory=list)


class CriterionSet(ArtifactEnvelope):
    artifact_type: Literal["CriterionSet"] = "CriterionSet"
    schema_version: Literal["1.0"] = "1.0"
    criteria: list[Criterion] = Field(default_factory=_empty_criteria)
    missing_fields: list[str] = Field(default_factory=list)


class GuardrailSet(ArtifactEnvelope):
    artifact_type: Literal["GuardrailSet"] = "GuardrailSet"
    schema_version: Literal["1.0"] = "1.0"
    guardrails: list[Guardrail] = Field(default_factory=_empty_guardrails)
    missing_fields: list[str] = Field(default_factory=list)


class ExecutionBudgetArtifact(ArtifactEnvelope):
    artifact_type: Literal["ExecutionBudgetArtifact"] = "ExecutionBudgetArtifact"
    schema_version: Literal["1.0"] = "1.0"
    budget: ExecutionBudget
    policy_version: str = Field(min_length=1)


class WorkloadIdentity(ArtifactEnvelope):
    artifact_type: Literal["WorkloadIdentity"] = "WorkloadIdentity"
    schema_version: Literal["1.0"] = "1.0"
    workload: WorkloadContract | None = None
    missing_fields: list[str] = Field(default_factory=list)


class EvidenceRequirementSet(ArtifactEnvelope):
    artifact_type: Literal["EvidenceRequirementSet"] = "EvidenceRequirementSet"
    schema_version: Literal["1.0"] = "1.0"
    evidence_requirements: list[EvidenceRequirement] = Field(
        default_factory=_empty_evidence_requirements
    )
    missing_fields: list[str] = Field(default_factory=list)


class A1ApprovalDecision(ArtifactEnvelope):
    artifact_type: Literal["A1ApprovalDecision"] = "A1ApprovalDecision"
    schema_version: Literal["1.0"] = "1.0"
    approved: bool
    approval: ApprovalBinding | None = None
    decision_route: Literal["continue", "approval", "rejected"]
    reasons: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)


class OptimizationRequest(ArtifactEnvelope):
    artifact_type: Literal["OptimizationRequest"] = "OptimizationRequest"
    schema_version: Literal["1.0"] = "1.0"
    origin: Origin
    scope_profile: ScopeProfile
    source: SourceReference
    objective: Objective
    criteria: list[Criterion] = Field(min_length=1)
    guardrails: list[Guardrail] = Field(default_factory=list["Guardrail"])
    workload: WorkloadContract
    evidence_requirements: list[EvidenceRequirement] = Field(min_length=1)
    budget: ExecutionBudget
    approval: ApprovalBinding
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
