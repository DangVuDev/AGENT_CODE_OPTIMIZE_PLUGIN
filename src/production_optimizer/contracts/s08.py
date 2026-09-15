"""Contract-only shape for S08 (Progressive Rollout) --
docs/project-blueprint/shared-workflow/08-rollout.md.

Deliberately contract-only: no `s08_handlers.py`, no orchestration subgraph,
no `S08_NODE_IDS` entry in `orchestration/catalog.py` (see that module's own
comment on `SHARED_WORKFLOW_NODE_IDS`). The doc itself is explicit that "for
a local-only tool, this stage requires an explicit deployment adapter; it
must not pretend that modifying a local working tree is production
rollout" -- this codebase has no `CONNECTED_PRODUCTION` deployment target
(no Argo Rollouts/Kubernetes/OpenFeature adapter, no real traffic-routing or
feature-flag integration), so implementing real S08 handlers today would
mean fabricating exactly the kind of fake rollout evidence the doc warns
against. These types exist so a future milestone that *does* have a real
deployment target can implement `s08_handlers.py` against an already-agreed
shape rather than designing the contract and the adapter at the same time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .envelope import ArtifactEnvelope


class ExposureStage(ContractModel):
    """One risk-ordered rollout stage (S08.20). BR-08-002: promotion changes
    exactly one stage at a time -- enforced by `RolloutPlan`'s own validator
    on `sequence`, not by this leaf type alone."""

    stage_id: str = Field(min_length=1)
    stage_kind: Literal["shadow", "internal", "canary", "gradual", "full"]
    sequence: int = Field(ge=1)
    cohort: str = Field(min_length=1)
    exposure_percentage: float = Field(ge=0, le=100)
    minimum_duration_seconds: int = Field(gt=0)
    promotion_thresholds: dict[str, float] = Field(default_factory=dict)


class RolloutPlan(ArtifactEnvelope):
    """S08.20's sealed output. BR-08-001: only ever built for a case whose
    `Decision.outcome` (`contracts/s06.py`) was a real KEEP -- `decision_digest`
    links to that exact sealed artifact, never a re-derived summary of it."""

    artifact_type: Literal["RolloutPlan"] = "RolloutPlan"
    schema_version: Literal["1.0"] = "1.0"
    decision_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    connected_production_profile: str = Field(min_length=1)
    stages: list[ExposureStage] = Field(min_length=1)
    rollback_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_stage_sequence(self) -> RolloutPlan:
        sequences = [stage.sequence for stage in self.stages]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            raise ValueError("exposure stages must have distinct, increasing sequence numbers")
        return self


class RollbackReadiness(ContractModel):
    """S08.40's real pre-exposure check -- exercised *before* any real
    traffic is shifted, not asserted after the fact."""

    exercised: bool
    recovery_time_seconds: float | None = None
    data_compatible: bool
    detail: str = Field(min_length=1)


class DeploymentEvent(ContractModel):
    """S08.50's record of one real, immutable deployment identity
    (BR-08-004) -- `artifact_digest` is the exact deployable artifact
    promoted, never a mutable tag or branch name."""

    stage_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    deployed_at: datetime


class StageObservation(ContractModel):
    """S08.60's real primary-metric/guardrail observation for one stage's
    declared window -- raw values, not a pre-aggregated verdict."""

    stage_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    value: float
    guardrail_breached: bool
    window_start: datetime
    window_end: datetime


class StageDecision(ContractModel):
    """S08.70's deterministic promote/hold/rollback verdict for one stage --
    BR-08-003: a guardrail breach forces `rollback` when policy says so,
    never left to model judgment."""

    stage_id: str = Field(min_length=1)
    outcome: Literal["promote", "hold", "rollback"]
    reason: str = Field(min_length=1)


class RollbackEvidence(ContractModel):
    """S08.80's real restoration record -- only produced when a
    `StageDecision.outcome` was actually `rollback`."""

    stage_id: str = Field(min_length=1)
    restored: bool
    verified_healthy: bool
    incident_id: str | None = None
    detail: str = Field(min_length=1)


class RolloutReport(ArtifactEnvelope):
    """S08.90's sealed handoff. BR-08-005: reaching full exposure alone is
    not `"completed"` -- that outcome requires the final observation window
    to have actually passed, not merely the last stage to have deployed."""

    artifact_type: Literal["RolloutReport"] = "RolloutReport"
    schema_version: Literal["1.0"] = "1.0"
    rollout_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    deployments: list[DeploymentEvent] = Field(min_length=1)
    observations: list[StageObservation] = Field(default_factory=list["StageObservation"])
    decisions: list[StageDecision] = Field(min_length=1)
    rollback_evidence: RollbackEvidence | None = None
    final_outcome: Literal["completed", "rolled_back", "held"]
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rollback_evidence_when_rolled_back(self) -> RolloutReport:
        if self.final_outcome == "rolled_back" and self.rollback_evidence is None:
            raise ValueError("a rolled_back outcome requires rollback_evidence")
        return self
