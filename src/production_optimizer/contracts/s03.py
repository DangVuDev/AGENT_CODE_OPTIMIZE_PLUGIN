from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import ContractModel
from .envelope import ArtifactEnvelope


class ToolCallRecord(ContractModel):
    """One step of S03.50's bounded tool loop (`application.s03_agent_loop`)."""

    step: int = Field(ge=1)
    tool: Literal["read_file", "write_file", "run_command", "done"]
    path: str | None = None
    command_kind: str | None = None
    summary: str = Field(min_length=1)


class ExecutionProvenance(ArtifactEnvelope):
    artifact_type: Literal["ExecutionProvenance"] = "ExecutionProvenance"
    schema_version: Literal["1.0"] = "1.0"
    execution_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    phase_id: str = Field(min_length=1)
    executor_kind: Literal["deterministic_config", "llm_driven"]
    model_id: str | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list["ToolCallRecord"])
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    base_revision: str = Field(min_length=1)
    isolation: Literal["git_worktree", "directory_copy"]


class ScopeViolation(ContractModel):
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ScopeReport(ContractModel):
    in_scope: bool
    violations: list[ScopeViolation] = Field(default_factory=list["ScopeViolation"])


class SanitationFinding(ContractModel):
    kind: Literal["secret", "forbidden_binary", "lockfile_change", "migration_change", "license"]
    detail: str = Field(min_length=1)


class SanitationReport(ContractModel):
    passed: bool
    findings: list[SanitationFinding] = Field(default_factory=list["SanitationFinding"])


class PatchArtifact(ArtifactEnvelope):
    """S03.80's sealed handoff. `diff` is a real unified diff (computed via
    `difflib`, not `git diff` -- works identically whether the isolated
    workspace is a real git worktree or a plain directory copy, see
    `s03_handlers._s03_20`'s docstring)."""

    artifact_type: Literal["PatchArtifact"] = "PatchArtifact"
    schema_version: Literal["1.0"] = "1.0"
    execution_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_provenance_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    phase_id: str = Field(min_length=1)
    task_ids: list[str] = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    changed_files: list[str] = Field(min_length=1)
    diff: str = Field(min_length=1)
    scope_report: ScopeReport
    sanitation_report: SanitationReport
