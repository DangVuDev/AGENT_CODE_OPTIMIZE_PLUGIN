from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope
from .evaluation import ComposeExecutionContract, EvaluationSpec


class Origin(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class ScopeProfile(StrEnum):
    LOCAL_SANDBOX = "local_sandbox"
    CONNECTED_PRODUCTION = "connected_production"


class CriterionInput(ContractModel):
    """One requester-declared optimization criterion, before A1.61
    canonicalizes it into a real `Criterion`.

    `weight` is the requester's own priority between criteria and is the only
    thing that orders them: S01.40 normalizes every weight against their sum
    (`weight / total_weight`) before scoring, so what matters is each
    criterion's share, not its absolute value or its position in the list.
    """

    metric_id: str = Field(min_length=1, max_length=255)
    direction: Literal["minimize", "maximize", "target"]
    target: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=100)
    weight: float = Field(default=1.0, gt=0)


class ManualCasePayload(ContractModel):
    """Manual A1 intake supporting raw, structured, and mixed requests."""

    local_path: str = Field(min_length=1, max_length=2048, description="repository root path")
    allowed_root: str = Field(
        min_length=1, max_length=2048, description="allowed root for security"
    )
    raw_text: str | None = Field(default=None, min_length=1, max_length=12_000)
    feature_id: str | None = Field(default=None, min_length=1, max_length=255)
    # Single-criterion shorthand. `criteria` below is the general form; these
    # four stay because most requests really do have one criterion, and every
    # existing caller (CLI flags, contract tests) is written against them.
    metric_id: str | None = Field(default=None, min_length=1, max_length=255)
    direction: Literal["minimize", "maximize", "target"] | None = None
    target: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=100)
    # BR-A1-002 requires *at least* one primary criterion, and S01's ranking
    # formula weights each one's normalized benefit -- so a request may carry
    # several. When non-empty this wins over the four shorthand fields above.
    criteria: list[CriterionInput] = Field(default_factory=list, max_length=32)
    workload_id: str | None = Field(default=None, min_length=1, max_length=255)
    environment_id: str | None = Field(default=None, min_length=1, max_length=255)
    command_id: str | None = Field(default=None, min_length=1, max_length=255)
    execution_profile: Literal["legacy_discovery", "docker_compose"] = "legacy_discovery"
    compose_file: str | None = Field(default=None, min_length=1, max_length=2048)
    application_services: list[str] = Field(default_factory=list, max_length=64)
    evaluations: list[EvaluationSpec] = Field(default_factory=list, max_length=64)
    actor_id: str = Field(min_length=1, max_length=255)
    actor_role: str = Field(default="requester", min_length=1, max_length=100)
    policy_version: str = Field(default="intake-policy-v1", min_length=1, max_length=100)
    guardrail_metric_id: str | None = Field(
        default=None, max_length=255, description="optional guardrail metric"
    )
    dataset_id: str | None = Field(
        default=None, max_length=255, description="optional dataset/benchmark"
    )
    requester_hypothesis: str | None = Field(default=None, max_length=2000)
    repetitions: int | None = Field(default=None, ge=1, le=100)
    warmup_runs: int | None = Field(default=None, ge=0, le=50)
    concurrency: int | None = Field(default=None, ge=1, le=256)
    cache_state: Literal["cold", "warm", "mixed"] | None = None
    deadline_seconds: int | None = Field(default=None, gt=0)
    maximum_worker_seconds: int | None = Field(default=None, ge=0)
    maximum_model_tokens: int | None = Field(default=None, ge=0)
    maximum_storage_bytes: int | None = Field(default=None, ge=0)
    allowed_analyzers: set[str] | None = None

    @model_validator(mode="after")
    def require_business_intent(self) -> ManualCasePayload:
        structured = (
            self.feature_id,
            self.metric_id,
            self.direction,
            self.target,
            self.unit,
            self.workload_id,
            self.environment_id,
            self.command_id,
        )
        if (
            self.raw_text is None
            and not self.criteria
            and not any(value is not None for value in structured)
        ):
            raise ValueError("raw_text or at least one structured business field is required")
        # Two criteria naming the same metric would collide downstream: A1.61
        # derives each `Criterion.criterion_id` from the metric, and A1.71
        # keys one `EvidenceRequirement` per criterion off that id.
        metric_ids = [criterion.metric_id for criterion in self.criteria]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("each criterion must name a distinct metric_id")
        if self.execution_profile == "docker_compose" and (
            self.compose_file is None or not self.evaluations
        ):
            raise ValueError(
                "docker_compose profile requires compose_file and at least one evaluation"
            )
        if self.execution_profile == "legacy_discovery" and (
            self.compose_file is not None or self.evaluations or self.application_services
        ):
            raise ValueError("Compose fields require execution_profile='docker_compose'")
        return self


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
    # Carries `ManualCasePayload.criteria` through to A1.61 unchanged. Empty
    # means the request used the single-criterion shorthand above instead.
    criteria: list[CriterionInput] = Field(default_factory=list, max_length=32)
    guardrail_metric_id: str | None = Field(default=None, max_length=255)
    workload_id: str | None = Field(default=None, max_length=255)
    dataset_id: str | None = Field(default=None, max_length=255)
    environment_id: str | None = Field(default=None, max_length=255)
    command_id: str | None = Field(default=None, max_length=255)
    extraction_confidence: float = Field(ge=0, le=1)
    unresolved_fields: list[str] = Field(default_factory=list)
    requester_hypothesis: str | None = Field(default=None, max_length=2000)
    field_provenance: dict[str, str] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)


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
    source_files: list[str] = Field(default_factory=list)
    generated_or_vendor_paths: list[str] = Field(default_factory=list)
    file_count: int = Field(ge=0)
    byte_count: int = Field(default=0, ge=0)
    discovery_truncated: bool = False
    unreadable_paths: list[str] = Field(default_factory=list)


class FeatureScope(ArtifactEnvelope):
    artifact_type: Literal["FeatureScope"] = "FeatureScope"
    schema_version: Literal["1.0"] = "1.0"
    feature_id: str = Field(min_length=1)
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=2000)
    supporting_paths: list[str] = Field(default_factory=list)
    alternative_paths: list[str] = Field(default_factory=list)


class A1QualityReport(ArtifactEnvelope):
    artifact_type: Literal["A1QualityReport"] = "A1QualityReport"
    schema_version: Literal["1.0"] = "1.0"
    passed: bool
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    invalid_values: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    repair_owners: dict[str, str] = Field(default_factory=dict)
    policy_version: str = Field(min_length=1)


class SourceReference(ContractModel):
    repository_id: str = Field(min_length=1)
    allowed_root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    requested_revision: str | None = None
    source_kind: str = Field(default="local_directory", min_length=1)
    locator: str | None = Field(default=None, min_length=1, max_length=4096)


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
    aggregation: Literal["mean", "p50", "p95", "p99", "sum", "rate", "maximum", "verdict"] = "mean"
    acceptance_operator: Literal["lt", "lte", "eq", "gte", "gt"] = "lte"
    tolerance: float = Field(default=0, ge=0)
    metric_schema_version: str = Field(default="1.0", min_length=1)


class Guardrail(ContractModel):
    guardrail_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    operator: Literal["lt", "lte", "eq", "gte", "gt"]
    threshold: float
    unit: str = Field(min_length=1)
    category: Literal["correctness", "security", "reliability", "cost", "quality"] = "correctness"
    severity: Literal["warning", "blocking"] = "blocking"
    mandatory: bool = True
    metric_schema_version: str = Field(default="1.0", min_length=1)


class WorkloadContract(ContractModel):
    workload_id: str = Field(min_length=1)
    dataset_id: str | None = None
    environment_id: str = Field(min_length=1)
    command_id: str | None = None
    repetitions: int = Field(ge=1)
    warmup_runs: int = Field(ge=0)
    concurrency: int = Field(ge=1)
    cache_state: str = Field(min_length=1)
    protocol_version: str = Field(default="1.0", min_length=1)
    dataset_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    parameters: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None


class EvidenceRequirement(ContractModel):
    requirement_id: str = Field(min_length=1)
    criterion_id: str
    accepted_source_types: set[str] = Field(min_length=1)
    minimum_samples: int = Field(ge=1)
    mandatory: bool = True
    metric_id: str | None = None
    canonical_unit: str | None = None
    aggregation: Literal["mean", "p50", "p95", "p99", "sum", "rate", "maximum", "verdict"] = "mean"
    required_dimensions: set[str] = Field(default_factory=set)
    freshness_seconds: int | None = Field(default=None, gt=0)
    minimum_trust_level: Literal["unverified", "verified", "attested"] = "verified"


class ExecutionBudget(ContractModel):
    deadline_seconds: int = Field(gt=0)
    maximum_worker_seconds: int = Field(ge=0)
    maximum_model_tokens: int = Field(ge=0)
    maximum_storage_bytes: int = Field(ge=0)
    allowed_analyzers: set[str] = Field(default_factory=set)
    maximum_retries: int = Field(default=1, ge=0, le=10)
    maximum_concurrency: int = Field(default=1, ge=1, le=256)
    priority: Literal["low", "normal", "high"] = "normal"


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
    execution: ComposeExecutionContract | None = None
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
    feature_scope: FeatureScope | None = None
    objective: Objective
    criteria: list[Criterion] = Field(min_length=1)
    guardrails: list[Guardrail] = Field(default_factory=list["Guardrail"])
    workload: WorkloadContract
    execution: ComposeExecutionContract | None = None
    evidence_requirements: list[EvidenceRequirement] = Field(min_length=1)
    budget: ExecutionBudget
    approval: ApprovalBinding
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
