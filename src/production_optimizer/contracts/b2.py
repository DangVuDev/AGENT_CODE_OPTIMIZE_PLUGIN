from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope


class AnalysisStrategy(ContractModel):
    strategy_id: str = Field(min_length=1)
    selected_analyzers: list[str] = Field(min_length=1)
    skipped_analyzers: dict[str, str] = Field(default_factory=dict)
    language_coverage: dict[str, float] = Field(default_factory=dict)
    risk_informed: bool = False


class TruncationReport(ContractModel):
    truncated: bool
    omitted_evidence_ids: list[str] = Field(default_factory=list)
    omitted_source_regions: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_reason_when_truncated(self) -> TruncationReport:
        if self.truncated and not self.reason:
            raise ValueError("a truncated context package requires a reason")
        return self


class ModelContextPackage(ContractModel):
    package_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    source_regions: list[str] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    criteria_ids: list[str] = Field(min_length=1)
    redacted_fields: list[str] = Field(default_factory=list)
    truncation_report: TruncationReport


class DiscoveryAssumption(ContractModel):
    assumption_id: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=2000)
    basis: Literal["detected_fact", "inferred_context"]
    verified: bool


class DiscoveryAssumptionReport(ContractModel):
    report_id: str = Field(min_length=1)
    feature_binding_confirmed: bool
    source_binding_confirmed: bool
    owner_binding_confirmed: bool
    assumptions: list[DiscoveryAssumption] = Field(default_factory=list["DiscoveryAssumption"])


class StalenessDecision(ContractModel):
    decision_id: str = Field(min_length=1)
    stale: bool
    changed_dimensions: list[str] = Field(default_factory=list)
    action: Literal["proceed", "refresh", "cancel"]

    @model_validator(mode="after")
    def validate_action_matches_staleness(self) -> StalenessDecision:
        if self.stale and self.action == "proceed":
            raise ValueError("a stale proposal cannot proceed without refresh or cancellation")
        if not self.stale and self.action != "proceed":
            raise ValueError("a fresh proposal must proceed")
        return self


class ProposalRoutingDecision(ContractModel):
    decision_id: str = Field(min_length=1)
    route: Literal["auto_forward", "owner_review", "security_review", "reject"]
    reasons: list[str] = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    required_actor_role: str | None = None

    @model_validator(mode="after")
    def validate_review_route_has_actor_role(self) -> ProposalRoutingDecision:
        needs_actor = self.route in {"owner_review", "security_review"}
        if needs_actor and not self.required_actor_role:
            raise ValueError(
                "an owner_review or security_review route requires required_actor_role"
            )
        return self


class ProposalApproval(ContractModel):
    approval_id: str | None = None
    actor_id: str | None = None
    actor_role: str | None = None
    decision: Literal["approved", "rejected", "revision_requested", "pending"]
    artifact_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1)


class ProposalEnvelope(ArtifactEnvelope):
    """Envelope: QualifiedOpportunity + FindingSet + SolutionPortfolio + routing/approval state.

    Constituent artifacts are referenced by content digest rather than
    embedded, following the same linkage convention as `BaselineSnapshot`
    and `EvidenceBundle` in `a2.py`. `b2.py` deliberately does not import
    `b1.py` or `a3.py` types to avoid a cross-lane import cycle; digest
    references are sufficient for the envelope's own validation.
    """

    artifact_type: Literal["ProposalEnvelope"] = "ProposalEnvelope"
    schema_version: Literal["1.0"] = "1.0"
    qualified_opportunity_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    finding_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    solution_portfolio_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    quality_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    routing_decision: ProposalRoutingDecision
    approval: ProposalApproval
    staleness: StalenessDecision
    narrative: str | None = None
