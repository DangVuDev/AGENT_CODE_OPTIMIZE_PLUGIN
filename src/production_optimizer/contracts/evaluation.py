from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel


class ContainerCommandSpec(ContractModel):
    """One repository-owned command executed inside a Compose service."""

    service: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.-]+$")
    argv: list[str] = Field(min_length=1, max_length=128)
    working_directory: str | None = Field(default=None, min_length=1, max_length=2048)
    timeout_seconds: int = Field(default=300, gt=0, le=86_400)

    @model_validator(mode="after")
    def reject_ambiguous_argv(self) -> ContainerCommandSpec:
        if any(not component or "\x00" in component for component in self.argv):
            raise ValueError("argv components must be non-empty and contain no NUL bytes")
        return self


class EvaluationSpec(ContractModel):
    """Repeatable evaluator and the metric names it promises to emit."""

    evaluation_id: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.-]+$")
    command: ContainerCommandSpec
    repetitions: int = Field(default=1, ge=1, le=100)
    warmup_runs: int = Field(default=0, ge=0, le=50)
    expected_metric_ids: set[str] = Field(min_length=1)


class ComposeExecutionContract(ContractModel):
    """Immutable A1-to-A2 handoff for isolated product evaluation."""

    compose_file: str = Field(min_length=1, max_length=2048)
    application_services: list[str] = Field(default_factory=list, max_length=64)
    evaluations: list[EvaluationSpec] = Field(min_length=1, max_length=64)
    startup_timeout_seconds: int = Field(default=180, gt=0, le=3600)
    cleanup_timeout_seconds: int = Field(default=60, gt=0, le=600)

    @model_validator(mode="after")
    def require_unique_evaluations_and_known_services(self) -> ComposeExecutionContract:
        ids = [item.evaluation_id for item in self.evaluations]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation_id values must be unique")
        if len(self.application_services) != len(set(self.application_services)):
            raise ValueError("application_services values must be unique")
        return self


MetricValue = float | int | bool | None


class EvaluationOutput(ContractModel):
    """The only accepted stdout payload from a product evaluator."""

    schema_version: str = Field(default="1.0", min_length=1, max_length=32)
    feature_id: str = Field(min_length=1, max_length=255)
    metrics: dict[str, MetricValue] = Field(min_length=1)
    samples: dict[str, list[MetricValue]] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationAttemptResult(ContractModel):
    evaluation_id: str = Field(min_length=1)
    repetition: int = Field(ge=1)
    exit_code: int
    output: EvaluationOutput | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class ComposeEvaluationWorkerResult(ContractModel):
    project_name: str = Field(min_length=1)
    attempts: list[EvaluationAttemptResult] = Field(default_factory=list)
    cleanup_confirmed: bool
    lifecycle_error: str | None = None
