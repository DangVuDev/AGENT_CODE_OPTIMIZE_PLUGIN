from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope


class SchemaValidationResult(ContractModel):
    artifact_type: str = Field(min_length=1)
    schema_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    valid: bool
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_errors_when_invalid(self) -> SchemaValidationResult:
        if not self.valid and not self.errors:
            raise ValueError("an invalid schema result requires errors")
        return self


class DigestChainLink(ContractModel):
    artifact_type: str = Field(min_length=1)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_parent_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    linked: bool


class DimensionEquivalenceVerdict(ContractModel):
    dimension: str = Field(min_length=1)
    equivalent: bool
    reason: str | None = None

    @model_validator(mode="after")
    def validate_reason_when_not_equivalent(self) -> DimensionEquivalenceVerdict:
        if not self.equivalent and not self.reason:
            raise ValueError("a non-equivalent dimension requires a reason")
        return self


class FreshnessCheck(ContractModel):
    dimension: str = Field(min_length=1)
    fresh: bool
    checked_at: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_expiry_order(self) -> FreshnessCheck:
        if self.expires_at is not None and self.expires_at <= self.checked_at:
            raise ValueError("expires_at must be after checked_at")
        return self


class ConvergenceDecision(ArtifactEnvelope):
    artifact_type: Literal["ConvergenceDecision"] = "ConvergenceDecision"
    schema_version: Literal["1.0"] = "1.0"
    origin: Literal["manual", "automatic"]
    schema_results: list[SchemaValidationResult] = Field(min_length=1)
    digest_chain: list[DigestChainLink] = Field(min_length=1)
    equivalence_verdicts: list[DimensionEquivalenceVerdict] = Field(min_length=1)
    freshness_checks: list[FreshnessCheck] = Field(min_length=1)
    eligible_solution_count: int = Field(ge=0)
    converged: bool
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_convergence_gate(self) -> ConvergenceDecision:
        schema_ok = all(result.valid for result in self.schema_results)
        digest_ok = all(link.linked for link in self.digest_chain)
        equivalence_ok = all(verdict.equivalent for verdict in self.equivalence_verdicts)
        freshness_ok = all(check.fresh for check in self.freshness_checks)
        eligible_ok = self.eligible_solution_count >= 1
        computed = schema_ok and digest_ok and equivalence_ok and freshness_ok and eligible_ok
        if self.converged != computed:
            raise ValueError(
                "converged must reflect the passed schema, digest, equivalence, "
                "freshness and eligibility gates"
            )
        if not self.converged and not self.reasons:
            raise ValueError("a failed convergence decision requires reasons")
        return self


class ConvergedCase(ArtifactEnvelope):
    """Envelope: origin plus validated references to request, baseline, evidence and portfolio.

    Constituent artifacts are referenced by content digest rather than
    embedded or imported, following the same linkage convention as
    `BaselineSnapshot.request_digest` in `a2.py` (which references
    `a1.py`'s `OptimizationRequest` without importing it).
    """

    artifact_type: Literal["ConvergedCase"] = "ConvergedCase"
    schema_version: Literal["1.0"] = "1.0"
    origin: Literal["manual", "automatic"]
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    baseline_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence_bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    solution_portfolio_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    convergence_decision_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
