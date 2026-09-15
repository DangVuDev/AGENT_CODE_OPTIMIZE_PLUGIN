# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any, cast

from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.shared_workflow import _policy_outcome


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
