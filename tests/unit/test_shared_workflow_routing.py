# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any, cast

from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.shared_workflow import (
    _MAX_PHASE_REPAIR_ATTEMPTS,
    _policy_outcome,
    _verification_outcome,
)


def _state(**overrides: Any) -> OptimizationState:
    base: dict[str, Any] = {
        "case_id": "OPT-1",
        "thread_id": "THREAD-1",
        "tenant_id": "TENANT-1",
        "completed_nodes": [],
        "node_routes": {},
        "s06_decision": {},
    }
    base.update(overrides)
    return cast("OptimizationState", base)


def test_policy_outcome_halts_when_s06_90_is_still_pending_approval() -> None:
    """Regression test for the real routing bug found while adding S06.90:
    `_policy_outcome` used to gate on `"S06.80" in completed_nodes`, which
    is already true the instant S06.90 first halts (`NodeRuntime._commit`
    records a node as completed the moment it runs at all, regardless of
    which route it returned) -- silently routing to "keep" while the real
    apply-to-real-repo approval was still pending."""

    state = _state(
        completed_nodes=["S06.10", "S06.20", "S06.80", "S06.90"],
        node_routes={"S06.90": "approval"},
        s06_decision={"outcome": "KEEP", "rationale": "all primary criteria met"},
    )

    assert _policy_outcome(state) == "halt"


def test_policy_outcome_proceeds_to_keep_once_s06_90_has_completed() -> None:
    state = _state(
        completed_nodes=["S06.10", "S06.20", "S06.80", "S06.90"],
        node_routes={"S06.90": "continue"},
        s06_decision={"outcome": "KEEP", "rationale": "all primary criteria met"},
    )

    assert _policy_outcome(state) == "keep"


def test_policy_outcome_halts_when_s06_90_never_ran() -> None:
    """S06.10 rejecting upstream means S06.20-S06.90 never run at all --
    `route_for` correctly defaults to "continue" for a node that never ran,
    but `s06_decision` is then empty too, so the existing fallback (not the
    new S06.90 check) is what still correctly returns "halt" here."""

    state = _state(completed_nodes=["S06.10"], node_routes={"S06.10": "rejected"})

    assert _policy_outcome(state) == "halt"


def test_shared_repair_budget_allows_a_fresh_fix_one_part_after_verification_failures() -> None:
    """`s03_revision_attempts` is a shared budget between S04.80
    verification failures and S06.50 FIX_ONE_PART decisions by deliberate
    choice (see `shared_workflow.py`'s own module docstring). This does not
    claim the sharing itself is "solved" -- only that the chosen cap
    (`_MAX_PHASE_REPAIR_ATTEMPTS`) leaves enough headroom for a realistic
    scenario: a couple of genuine verification retries followed by one
    fresh, unrelated FIX_ONE_PART decision, without the shared budget
    already being exhausted by the time the fix is attempted."""

    attempts_after_two_verification_failures = 2
    assert attempts_after_two_verification_failures < _MAX_PHASE_REPAIR_ATTEMPTS

    state = _state(
        completed_nodes=["S04.90"],
        s04_manifest={"gate_passed": False},
        s03_revision_attempts=attempts_after_two_verification_failures,
    )
    # A fresh verification attempt is still allowed to retry, not halted --
    # the budget was not starved by the two prior verification failures
    # alone.
    assert _verification_outcome(state) == "revision"

    fix_one_part_state = _state(
        completed_nodes=["S06.10", "S06.20", "S06.80", "S06.90"],
        node_routes={"S06.90": "continue"},
        s06_decision={"outcome": "FIX_ONE_PART", "rationale": "a localized regression"},
        s03_revision_attempts=attempts_after_two_verification_failures,
    )
    # The real point of this test: a fresh, never-before-tried FIX_ONE_PART
    # decision is still allowed to proceed rather than immediately halting,
    # because the shared budget wasn't exhausted by the verification
    # failures alone under the current cap.
    assert _policy_outcome(fix_one_part_state) == "fix_one_part"


def test_shared_repair_budget_halts_once_actually_exhausted() -> None:
    """The cap is still real and still enforced -- once
    `s03_revision_attempts` reaches `_MAX_PHASE_REPAIR_ATTEMPTS`, both
    verification retries and FIX_ONE_PART decisions correctly halt rather
    than looping forever (BR-04-004 / the "all loops are bounded"
    requirement), regardless of which failure class contributed the
    attempts."""

    state = _state(
        completed_nodes=["S04.90"],
        s04_manifest={"gate_passed": False},
        s03_revision_attempts=_MAX_PHASE_REPAIR_ATTEMPTS,
    )
    assert _verification_outcome(state) == "halt"

    fix_one_part_state = _state(
        completed_nodes=["S06.10", "S06.20", "S06.80", "S06.90"],
        node_routes={"S06.90": "continue"},
        s06_decision={"outcome": "FIX_ONE_PART", "rationale": "a localized regression"},
        s03_revision_attempts=_MAX_PHASE_REPAIR_ATTEMPTS,
    )
    assert _policy_outcome(fix_one_part_state) == "halt"
