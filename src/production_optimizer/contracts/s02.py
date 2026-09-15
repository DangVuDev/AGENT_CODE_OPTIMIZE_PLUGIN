from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope


class PlanTreatment(ContractModel):
    """Deliberately self-contained (not `contracts.a3.Treatment`, though the
    shape matches) -- S02's contracts never import a3.py, mirroring why
    `contracts.b1` defines its own leaf types instead of reusing A2's."""

    variable: str = Field(min_length=1)
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)


class ExecutionPhase(ContractModel):
    phase_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    phase_kind: Literal["diagnostic", "implementation"]
    treatment: PlanTreatment
    done_criteria: list[str] = Field(min_length=1)
    rollback_command: str | None = None
    rollback_trigger: str = Field(min_length=1)
    rollback_deadline_seconds: int = Field(gt=0)


class PlanTask(ContractModel):
    task_id: str = Field(min_length=1)
    phase_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    proposed_creation: bool = False
    instructions: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    status: Literal["pending", "ready", "in_progress", "done", "blocked"] = "pending"


class ExecutionPlan(ArtifactEnvelope):
    artifact_type: Literal["ExecutionPlan"] = "ExecutionPlan"
    schema_version: Literal["1.0"] = "1.0"
    selected_solution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    phases: list[ExecutionPhase] = Field(min_length=1)


class TaskList(ArtifactEnvelope):
    artifact_type: Literal["TaskList"] = "TaskList"
    schema_version: Literal["1.0"] = "1.0"
    execution_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tasks: list[PlanTask] = Field(min_length=1)


class PlanQualityResult(ContractModel):
    dimension: str = Field(min_length=1)
    passed: bool
    detail: str | None = None


class PlanQualityReport(ArtifactEnvelope):
    """`execution_plan_digest`/`task_list_digest` are `None` only when the
    draft was too broken to produce a valid `ExecutionPlan`/`TaskList` at all
    (e.g. zero well-formed phases) -- the report still exists and explains
    why in `results`, but there is nothing real yet to reference; fabricating
    a placeholder digest would misrepresent that failure as success."""

    artifact_type: Literal["PlanQualityReport"] = "PlanQualityReport"
    schema_version: Literal["1.0"] = "1.0"
    execution_plan_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    task_list_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    passed: bool
    results: list[PlanQualityResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_passed_matches_results(self) -> PlanQualityReport:
        computed = all(result.passed for result in self.results)
        if self.passed != computed:
            raise ValueError("passed must reflect every quality dimension")
        if self.passed and (self.execution_plan_digest is None or self.task_list_digest is None):
            raise ValueError("a passed report must reference a real ExecutionPlan and TaskList")
        return self


class PlanApproval(ContractModel):
    decision: Literal["auto_approved", "approved", "revision_requested", "rejected", "pending"]
    actor_id: str | None = None
    actor_role: str | None = None
    policy_version: str = Field(min_length=1)
