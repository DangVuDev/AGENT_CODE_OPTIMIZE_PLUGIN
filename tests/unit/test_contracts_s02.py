from __future__ import annotations

import pytest
from pydantic import ValidationError

from production_optimizer.contracts.s02 import ExecutionPhase, PlanTreatment


def _phase_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "phase_id": "phase-1",
        "sequence": 1,
        "phase_kind": "implementation",
        "risk_tier": "code",
        "treatment": PlanTreatment(variable="x", before="1", after="2"),
        "done_criteria": ["done"],
        "rollback_trigger": "regression detected",
        "rollback_deadline_seconds": 600,
    }
    kwargs.update(overrides)
    return kwargs


def test_execution_phase_requires_risk_tier() -> None:
    """A model response that omits `risk_tier` must fail validation loudly
    rather than silently defaulting to `"code"` -- an omission should never
    be indistinguishable from an explicit, correct declaration of the
    lowest-but-one risk tier."""

    kwargs = _phase_kwargs()
    del kwargs["risk_tier"]

    with pytest.raises(ValidationError, match="risk_tier"):
        ExecutionPhase.model_validate(kwargs)


def test_execution_phase_rejects_an_invalid_risk_tier_value() -> None:
    with pytest.raises(ValidationError, match="risk_tier"):
        ExecutionPhase.model_validate(_phase_kwargs(risk_tier="not-a-real-tier"))


def test_execution_phase_accepts_every_valid_risk_tier() -> None:
    for tier in ("experiment_config", "prompt", "code", "architecture"):
        phase = ExecutionPhase.model_validate(_phase_kwargs(risk_tier=tier))
        assert phase.risk_tier == tier
