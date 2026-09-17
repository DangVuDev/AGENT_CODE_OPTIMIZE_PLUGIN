from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope


class CheckResult(ContractModel):
    """`coverage_percent` is populated only for a real `kind="unit"` run
    where the repository actually declares `pytest-cov` (see
    `s04_worker_capabilities.build_s04_capabilities`) -- `None` otherwise,
    never fabricated, per the spec's own S04.90 row ("Store commands,
    outputs, versions, durations, coverage and decision")."""

    command_id: str = Field(min_length=1)
    kind: Literal["build", "lint", "type", "unit", "integration", "security", "domain"]
    exit_code: int
    passed: bool
    duration_seconds: float = Field(ge=0)
    output_tail: str = Field(default="", max_length=4000)
    coverage_percent: float | None = Field(default=None, ge=0, le=100)


class FailureAttribution(ContractModel):
    check_kind: str = Field(min_length=1)
    classification: Literal[
        "patch_regression", "baseline_existing_failure", "flaky", "environment_failure",
        "tool_failure",
    ]
    detail: str = Field(min_length=1)


class VerificationReport(ArtifactEnvelope):
    """S04.90's sealed handoff. `passed` requires every *mandatory* check
    (build/lint/type/unit, plus integration/security/domain when the
    manifest resolved one) to have passed -- a guardrail/mandatory-check
    failure always fails closed (BR-04-005: performance cannot excuse it),
    regardless of how many optional checks passed."""

    artifact_type: Literal["VerificationReport"] = "VerificationReport"
    schema_version: Literal["1.0"] = "1.0"
    patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_provenance_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    phase_id: str = Field(min_length=1)
    check_results: list[CheckResult] = Field(min_length=1)
    failure_attributions: list[FailureAttribution] = Field(
        default_factory=list["FailureAttribution"]
    )
    passed: bool

    @model_validator(mode="after")
    def validate_passed_matches_checks(self) -> VerificationReport:
        mandatory_kinds = {"build", "lint", "type", "unit"}
        mandatory_results = [r for r in self.check_results if r.kind in mandatory_kinds]
        computed = bool(mandatory_results) and all(r.passed for r in self.check_results)
        if self.passed != computed:
            raise ValueError("passed must reflect every check result, mandatory checks required")
        return self
