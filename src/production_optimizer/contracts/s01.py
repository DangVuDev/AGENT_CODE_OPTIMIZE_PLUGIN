from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import ContractModel
from .envelope import ArtifactEnvelope


class StrategyScore(ContractModel):
    """S01.30-40's normalized-then-weighted score for one candidate strategy.

    `criterion_scores` keys are `Criterion.criterion_id` values from
    `contracts.a1.OptimizationRequest`; each value is that criterion's
    weighted contribution (already multiplied by the criterion's weight),
    so `sum(criterion_scores.values())` plus the effort/reversibility/risk
    terms below equals `total_score`.
    """

    strategy_id: str = Field(min_length=1)
    risk_tier: Literal["experiment_config", "prompt", "code", "architecture"]
    criterion_scores: dict[str, float] = Field(default_factory=dict)
    effort_score: float = Field(ge=0, le=1)
    reversibility_score: float = Field(ge=0, le=1)
    risk_penalty: float = Field(ge=0, le=1)
    total_score: float


class RankingResult(ArtifactEnvelope):
    """S01.50-60's sealed ranking, published once at S01.80.

    `sensitivity_flags` (S01.60) is non-empty when a bounded per-criterion
    weight perturbation changes the top-ranked strategy -- BR-01-004 reads
    this to decide whether the pick needs human approval.
    """

    artifact_type: Literal["RankingResult"] = "RankingResult"
    schema_version: Literal["1.0"] = "1.0"
    solution_portfolio_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scores: list[StrategyScore] = Field(min_length=1)
    ranked_strategy_ids: list[str] = Field(min_length=1)
    sensitivity_flags: list[str] = Field(default_factory=list)


class SelectionApproval(ContractModel):
    decision: Literal["auto_selected", "approved", "rejected", "pending"]
    actor_id: str | None = None
    actor_role: str | None = None
    policy_version: str = Field(min_length=1)


class SelectedSolution(ArtifactEnvelope):
    """S01.90's sealed handoff to S02 -- binds `strategy_id` plus the digest
    of the `SolutionPortfolio` and `RankingResult` it was chosen from
    (BR-01-002: a numeric display rank is never itself the binding)."""

    artifact_type: Literal["SelectedSolution"] = "SelectedSolution"
    schema_version: Literal["1.0"] = "1.0"
    ranking_result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    solution_portfolio_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_id: str = Field(min_length=1)
    approval: SelectionApproval
    excluded_strategy_ids: list[str] = Field(default_factory=list)
