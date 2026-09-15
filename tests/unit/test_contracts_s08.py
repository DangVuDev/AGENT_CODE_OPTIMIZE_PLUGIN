from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.s08 import (
    DeploymentEvent,
    ExposureStage,
    RollbackEvidence,
    RolloutPlan,
    RolloutReport,
    StageDecision,
    StageObservation,
)


def _digest(character: str = "a") -> str:
    return f"sha256:{character * 64}"


def _producer() -> ProducerIdentity:
    return ProducerIdentity(name="s08-service", version="1.0.0")


def _at() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _stage(sequence: int = 1) -> ExposureStage:
    return ExposureStage(
        stage_id=f"stage-{sequence}", stage_kind="canary", sequence=sequence, cohort="internal",
        exposure_percentage=5.0, minimum_duration_seconds=3600,
    )


def _rollout_plan_kwargs(*, stages: list[ExposureStage] | None = None) -> dict[str, Any]:
    return {
        "artifact_id": "ART-RP-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _at(),
        "producer": _producer(),
        "content_digest": _digest("b"),
        "decision_digest": _digest("c"),
        "connected_production_profile": "prod-us-east",
        "stages": stages if stages is not None else [_stage(1), _stage(2)],
        "rollback_plan_digest": _digest("d"),
    }


def _deployment_event(stage_id: str = "stage-1") -> DeploymentEvent:
    return DeploymentEvent(
        stage_id=stage_id, deployment_id="deploy-1", artifact_digest=_digest("e"), deployed_at=_at()
    )


def _stage_decision(outcome: str = "promote") -> StageDecision:
    return StageDecision(
        stage_id="stage-1", outcome=outcome, reason="guardrails held within threshold"  # type: ignore[arg-type]
    )


def _rollout_report_kwargs(
    *, final_outcome: str = "completed", rollback_evidence: RollbackEvidence | None = None
) -> dict[str, Any]:
    return {
        "artifact_id": "ART-RR-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _at(),
        "producer": _producer(),
        "content_digest": _digest("f"),
        "rollout_plan_digest": _digest("0"),
        "deployments": [_deployment_event()],
        "decisions": [_stage_decision()],
        "rollback_evidence": rollback_evidence,
        "final_outcome": final_outcome,
        "policy_version": "s08-rollout-v1",
    }


def test_rollout_plan_construction_and_pinned_fields() -> None:
    plan = RolloutPlan(**_rollout_plan_kwargs())
    assert plan.artifact_type == "RolloutPlan"
    assert plan.schema_version == "1.0"
    assert len(plan.stages) == 2


def test_rollout_plan_requires_at_least_one_stage() -> None:
    kwargs = _rollout_plan_kwargs()
    kwargs["stages"] = []
    with pytest.raises(ValidationError, match="stages"):
        RolloutPlan(**kwargs)


def test_rollout_plan_rejects_out_of_order_stage_sequence() -> None:
    kwargs = _rollout_plan_kwargs(stages=[_stage(2), _stage(1)])
    with pytest.raises(ValidationError, match="distinct, increasing sequence numbers"):
        RolloutPlan(**kwargs)


def test_rollout_plan_rejects_duplicate_stage_sequence() -> None:
    kwargs = _rollout_plan_kwargs(stages=[_stage(1), _stage(1)])
    with pytest.raises(ValidationError, match="distinct, increasing sequence numbers"):
        RolloutPlan(**kwargs)


def test_rollout_plan_rejects_malformed_digest() -> None:
    kwargs = _rollout_plan_kwargs()
    kwargs["decision_digest"] = "not-a-digest"
    with pytest.raises(ValidationError, match="decision_digest"):
        RolloutPlan(**kwargs)


def test_rollout_report_construction_and_pinned_fields() -> None:
    report = RolloutReport(**_rollout_report_kwargs())
    assert report.artifact_type == "RolloutReport"
    assert report.schema_version == "1.0"
    assert report.final_outcome == "completed"
    assert report.rollback_evidence is None


def test_rollout_report_requires_at_least_one_deployment() -> None:
    kwargs = _rollout_report_kwargs()
    kwargs["deployments"] = []
    with pytest.raises(ValidationError, match="deployments"):
        RolloutReport(**kwargs)


def test_rollout_report_rolled_back_requires_rollback_evidence() -> None:
    kwargs = _rollout_report_kwargs(final_outcome="rolled_back")
    with pytest.raises(ValidationError, match="rolled_back outcome requires rollback_evidence"):
        RolloutReport(**kwargs)

    evidence = RollbackEvidence(
        stage_id="stage-1", restored=True, verified_healthy=True,
        detail="traffic reverted to the prior stable version",
    )
    kwargs = _rollout_report_kwargs(final_outcome="rolled_back", rollback_evidence=evidence)
    report = RolloutReport(**kwargs)
    assert report.rollback_evidence is not None
    assert report.rollback_evidence.restored is True


def test_stage_observation_construction() -> None:
    observation = StageObservation(
        stage_id="stage-1", metric_id="p95_latency_ms", value=180.5, guardrail_breached=False,
        window_start=_at(), window_end=_at(),
    )
    assert observation.guardrail_breached is False
