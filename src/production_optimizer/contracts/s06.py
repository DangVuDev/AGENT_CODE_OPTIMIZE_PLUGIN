from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope


class TargetEvaluation(ContractModel):
    """One A1 primary criterion judged against S05's real `EffectResult` --
    S06.20's real, deterministic application of `Criterion.direction`/
    `target` (never an LLM judgment; the doc reserves that for S06.80's
    narrative only)."""

    criterion_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    direction: Literal["minimize", "maximize", "target"]
    target: float
    treatment_value: float
    met: bool


class GuardrailEvaluation(ContractModel):
    guardrail_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    evidence_source: Literal["verification_check", "statistical_effect", "unavailable"]
    observed_value: float | None = None
    passed: bool
    detail: str = Field(min_length=1)


class PhaseAttribution(ContractModel):
    phase_id: str = Field(min_length=1)
    classification: Literal["all_targets_met", "repairable", "wrong_direction", "no_evidence"]
    rationale: str = Field(min_length=1)


class RollbackReport(ArtifactEnvelope):
    """S06.60's sealed rollback evidence -- only produced for a real REVERT.
    S03's own workspace isolation (a git worktree or plain copy, never the
    real `SourceSnapshot.canonical_path_ref` itself -- see
    `s03_handlers._s03_20`'s docstring) means the original repository was
    never actually mutated, so "restoration" here is a real verification
    that it still isn't, not a fabricated git-revert claim."""

    artifact_type: Literal["RollbackReport"] = "RollbackReport"
    schema_version: Literal["1.0"] = "1.0"
    phase_id: str = Field(min_length=1)
    patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    original_untouched: bool
    detail: str = Field(min_length=1)


class Decision(ArtifactEnvelope):
    """S06.80's sealed handoff -- the one deterministic verdict every phase
    attempt ends in. `outcome` mirrors the doc's decision table exactly;
    `rollback_report_digest` is set only when `outcome == "REVERT"`."""

    artifact_type: Literal["Decision"] = "Decision"
    schema_version: Literal["1.0"] = "1.0"
    phase_id: str = Field(min_length=1)
    measurement_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    verification_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    target_evaluations: list[TargetEvaluation] = Field(min_length=1)
    guardrail_evaluations: list[GuardrailEvaluation] = Field(
        default_factory=list["GuardrailEvaluation"]
    )
    attribution: PhaseAttribution
    outcome: Literal["KEEP", "FIX_ONE_PART", "REVERT", "ESCALATE"]
    rationale: str = Field(min_length=1)
    rollback_report_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1)


class RepositoryApplyResult(ArtifactEnvelope):
    """S06.90's sealed handoff -- whether (and how) a real KEEP decision's
    diff was actually applied to the user's real repository. Deliberately
    NOT named/modeled as anything S08/"Rollout"-adjacent (see
    `contracts.s08`'s own docstring on why that identity is reserved for a
    real `CONNECTED_PRODUCTION` deployment target this milestone does not
    have): this is "commit an accepted local change into the user's real
    git history," never "roll it out to production traffic."

    `applied=False` is a legitimate, honestly-reportable outcome (drift, a
    failed `git apply --check`, a denied/rejected approval) -- not a
    case-ending failure; S06.90 always routes CONTINUE to S07 regardless of
    `applied`, matching this artifact's job of recording the truth, not
    gatekeeping the case."""

    artifact_type: Literal["RepositoryApplyResult"] = "RepositoryApplyResult"
    schema_version: Literal["1.0"] = "1.0"
    phase_id: str = Field(min_length=1)
    patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    applied: bool
    branch_name: str | None = None
    commit_sha: str | None = None
    base_revision: str = Field(min_length=1)
    failure_reason: str | None = None
    approval_decision: str = Field(min_length=1)
    approval_actor_id: str | None = None
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def applied_requires_commit_identity(self) -> RepositoryApplyResult:
        if self.applied and (self.branch_name is None or self.commit_sha is None):
            raise ValueError("applied=True requires both branch_name and commit_sha")
        if not self.applied and self.failure_reason is None:
            raise ValueError("applied=False requires a failure_reason")
        return self
