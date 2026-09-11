from __future__ import annotations

import pytest

from production_optimizer.adapters.production.python_policy import DeterministicPythonPolicy
from production_optimizer.contracts.platform import PolicyDecision, PolicyRequest

POLICY_VERSION = "bootstrap-v1"


def _approve_low_risk(request: PolicyRequest) -> PolicyDecision:
    """Toy illustrative decision: approve only when facts['risk'] == 'low'."""

    if request.facts.get("risk") == "low":
        return PolicyDecision(
            allowed=True,
            decision="approve",
            policy_version=POLICY_VERSION,
            reasons=["risk is low"],
        )
    return PolicyDecision(
        allowed=False,
        decision="deny",
        policy_version=POLICY_VERSION,
        reasons=["risk is not low"],
    )


def _request(decision_type: str, facts: dict[str, object]) -> PolicyRequest:
    return PolicyRequest(
        decision_type=decision_type,
        policy_version=POLICY_VERSION,
        tenant_id="tenant-a",
        facts=facts,
    )


def test_unregistered_decision_type_fails_closed() -> None:
    policy = DeterministicPythonPolicy(policy_version=POLICY_VERSION)

    decision = policy.evaluate(_request("unregistered", {}))

    assert decision == PolicyDecision(
        allowed=False,
        decision="deny",
        policy_version=POLICY_VERSION,
        reasons=["business policies are not implemented"],
    )


def test_bootstrap_decision_type_is_fail_closed_by_default() -> None:
    policy = DeterministicPythonPolicy(policy_version=POLICY_VERSION)

    decision = policy.evaluate(_request("bootstrap", {}))

    assert decision.allowed is False
    assert decision.decision == "deny"
    assert decision.reasons == ["business policies are not implemented"]


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            {"risk": "low"},
            PolicyDecision(
                allowed=True,
                decision="approve",
                policy_version=POLICY_VERSION,
                reasons=["risk is low"],
            ),
        ),
        (
            {"risk": "high"},
            PolicyDecision(
                allowed=False,
                decision="deny",
                policy_version=POLICY_VERSION,
                reasons=["risk is not low"],
            ),
        ),
        (
            {},
            PolicyDecision(
                allowed=False,
                decision="deny",
                policy_version=POLICY_VERSION,
                reasons=["risk is not low"],
            ),
        ),
    ],
)
def test_registered_decision_function_golden_outputs(
    facts: dict[str, object], expected: PolicyDecision
) -> None:
    policy = DeterministicPythonPolicy(policy_version=POLICY_VERSION)
    policy.register("risk_approval", _approve_low_risk)

    assert policy.evaluate(_request("risk_approval", facts)) == expected


def test_register_overrides_a_prior_registration() -> None:
    policy = DeterministicPythonPolicy(policy_version=POLICY_VERSION)
    policy.register("risk_approval", _approve_low_risk)

    def _always_deny(request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            decision="deny",
            policy_version=POLICY_VERSION,
            reasons=["always deny"],
        )

    policy.register("risk_approval", _always_deny)

    decision = policy.evaluate(_request("risk_approval", {"risk": "low"}))

    assert decision.reasons == ["always deny"]


def test_mismatched_policy_version_from_registered_function_raises() -> None:
    policy = DeterministicPythonPolicy(policy_version=POLICY_VERSION)

    def _wrong_version(request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            decision="approve",
            policy_version="some-other-version",
            reasons=["oops"],
        )

    policy.register("risk_approval", _wrong_version)

    with pytest.raises(ValueError, match="policy_version"):
        policy.evaluate(_request("risk_approval", {}))


def test_constructor_rejects_empty_policy_version() -> None:
    with pytest.raises(ValueError, match="policy_version"):
        DeterministicPythonPolicy(policy_version="  ")


def test_register_rejects_empty_decision_type() -> None:
    policy = DeterministicPythonPolicy(policy_version=POLICY_VERSION)
    with pytest.raises(ValueError, match="decision_type"):
        policy.register("  ", _approve_low_risk)


def test_healthcheck_always_true() -> None:
    policy = DeterministicPythonPolicy(policy_version=POLICY_VERSION)
    assert policy.healthcheck() is True
