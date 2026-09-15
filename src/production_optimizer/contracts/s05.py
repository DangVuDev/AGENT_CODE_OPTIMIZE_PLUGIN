from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import ContractModel
from .envelope import ArtifactEnvelope


class IsolationViolation(ContractModel):
    kind: Literal["unexpected_file_change", "dependency_drift", "scope_violation"]
    detail: str = Field(min_length=1)


class IsolationReport(ArtifactEnvelope):
    """S05.20's sealed check that the approved patch is the only logical
    difference between baseline and treatment (BR-05-001). Grounded in S03's
    own real `PatchArtifact.scope_report`/`changed_files` -- never a second,
    independent git diff against a workspace S04 may already have cleaned up
    on a real pass."""

    artifact_type: Literal["IsolationReport"] = "IsolationReport"
    schema_version: Literal["1.0"] = "1.0"
    patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    isolated: bool
    violations: list[IsolationViolation] = Field(default_factory=list["IsolationViolation"])


class EffectResult(ContractModel):
    """One criterion's raw, uninterpreted effect size. Deliberately carries
    no pass/fail verdict: BR-05's own doc reserves "business significance"
    for A1 policy, applied downstream by S06.20 -- S05.80 only computes the
    numbers."""

    criterion_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    baseline_mean: float
    treatment_mean: float
    absolute_change: float
    relative_change: float | None = None
    sample_count: int = Field(ge=1)


class StatisticalReport(ArtifactEnvelope):
    artifact_type: Literal["StatisticalReport"] = "StatisticalReport"
    schema_version: Literal["1.0"] = "1.0"
    effects: list[EffectResult] = Field(min_length=1)


class Measurement(ArtifactEnvelope):
    """S05.90's sealed handoff -- binds baseline, patch, protocol and every
    other S05 report by digest (BR-05-002: raw aggregates are retained in
    the reports themselves, never recomputed downstream)."""

    artifact_type: Literal["Measurement"] = "Measurement"
    schema_version: Literal["1.0"] = "1.0"
    baseline_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    isolation_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    comparability_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    statistical_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    phase_id: str = Field(min_length=1)
