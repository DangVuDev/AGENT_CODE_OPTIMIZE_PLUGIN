from __future__ import annotations

from typing import Any

from production_optimizer.adapters.production.bootstrap_policies import build_bootstrap_policy
from production_optimizer.contracts.platform import PolicyRequest

_POLICY_VERSION = "test-v1"


def _policy() -> Any:
    return build_bootstrap_policy(policy_version=_POLICY_VERSION)


def _request(decision_type: str, facts: dict[str, Any]) -> PolicyRequest:
    return PolicyRequest(
        decision_type=decision_type,
        policy_version=_POLICY_VERSION,
        tenant_id="TENANT-A",
        facts=facts,
    )


def _execution_facts(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "argv": ["/usr/bin/python", "-m", "pytest"],
        "working_directory": "/repo",
        "kind": "unit",
        "timeout_seconds": 300,
    }
    facts.update(overrides)
    return facts


def test_unknown_decision_type_is_denied() -> None:
    """The property an allow-everything stub could never have: a decision
    type nobody registered must not execute under an implicit allow."""

    decision = _policy().evaluate(_request("some.future.decision", {"anything": True}))

    assert decision.allowed is False
    assert decision.decision == "deny"


def test_authorizes_a_real_detected_command() -> None:
    decision = _policy().evaluate(_request("a2.execution_authorization", _execution_facts()))

    assert decision.allowed is True
    assert decision.policy_version == _POLICY_VERSION
    assert decision.reasons


def test_authorizes_a_declared_build_command() -> None:
    """Regression test for a real crash: `a2_31_*.py` was given a new
    `kind="build"` (for repositories -- e.g. Go -- with a `WorkloadContract.
    commands["build"]` declaration but no Python build convention to detect)
    without updating this fail-closed policy's own `_ALLOWED_COMMAND_KINDS`
    allowlist to match. A2.50 authorizes every `VerificationManifest.
    commands` entry through this exact policy, so a real, correctly-detected
    `build` command was silently denied -- `authorized=False` routed A2.50
    to `REJECTED`, and the whole case halted right there with no
    `BaselineSnapshot`, no error message pointing at the real cause."""

    decision = _policy().evaluate(
        _request("a2.execution_authorization", _execution_facts(kind="build"))
    )

    assert decision.allowed is True


def test_denies_unknown_command_kind() -> None:
    decision = _policy().evaluate(
        _request("a2.execution_authorization", _execution_facts(kind="deploy"))
    )

    assert decision.allowed is False
    assert any("kind" in reason for reason in decision.reasons)


def test_denies_argv_carrying_shell_metacharacters() -> None:
    """Nothing runs argv through a shell, so a metacharacter means the argv
    did not come from the detector that is supposed to build it."""

    decision = _policy().evaluate(
        _request(
            "a2.execution_authorization",
            _execution_facts(argv=["/usr/bin/python", "-m", "pytest; rm -rf /"]),
        )
    )

    assert decision.allowed is False
    assert any("shell metacharacters" in reason for reason in decision.reasons)


def test_denies_empty_argv_and_missing_working_directory() -> None:
    decision = _policy().evaluate(
        _request("a2.execution_authorization", _execution_facts(argv=[], working_directory="  "))
    )

    assert decision.allowed is False
    assert len(decision.reasons) == 2


def test_denies_out_of_range_timeout() -> None:
    for timeout in (0, -5, 10_000):
        decision = _policy().evaluate(
            _request("a2.execution_authorization", _execution_facts(timeout_seconds=timeout))
        )
        assert decision.allowed is False, timeout


def test_collector_authorization_checks_source_type() -> None:
    policy = _policy()

    allowed = policy.evaluate(
        _request(
            "a2.collector_authorization",
            {"collector_id": "a2-local-worker-broker", "source_type": "benchmark"},
        )
    )
    denied = policy.evaluate(
        _request(
            "a2.collector_authorization",
            {"collector_id": "a2-local-worker-broker", "source_type": "hearsay"},
        )
    )

    assert allowed.allowed is True
    assert denied.allowed is False


def test_collector_authorization_accepts_versioned_registry_recipe() -> None:
    decision = _policy().evaluate(
        _request(
            "a2.collector_authorization",
            {
                "collector_id": "campaign-evaluator",
                "source_type": "business-evaluation",
                "recipe_id": "campaign-value",
                "recipe_version": "7.2.0",
                "registry_version": 7,
                "executor_capability": "evaluate.campaign-value",
            },
        )
    )

    assert decision.allowed is True


def test_phase_authorization_requires_identifiers() -> None:
    policy = _policy()

    allowed = policy.evaluate(
        _request("s03_phase_authorization", {"phase_id": "phase-1", "case_id": "OPT-1"})
    )
    denied = policy.evaluate(
        _request("s03_phase_authorization", {"phase_id": "", "case_id": "OPT-1"})
    )

    assert allowed.allowed is True
    assert denied.allowed is False


def test_apply_to_real_repo_requires_identifiers() -> None:
    policy = _policy()
    base_facts = {"phase_id": "phase-1", "case_id": "OPT-1", "base_revision": "a" * 40}

    allowed = policy.evaluate(_request("apply_to_real_repo", base_facts))
    denied_phase = policy.evaluate(_request("apply_to_real_repo", {**base_facts, "phase_id": ""}))
    denied_revision = policy.evaluate(
        _request("apply_to_real_repo", {**base_facts, "base_revision": ""})
    )

    assert allowed.allowed is True
    assert denied_phase.allowed is False
    assert any("phase_id" in reason for reason in denied_phase.reasons)
    assert denied_revision.allowed is False
    assert any("base_revision" in reason for reason in denied_revision.reasons)


def test_apply_to_real_repo_requires_approval_by_default() -> None:
    """No risk-tier signal exists yet that would justify a bare `allow` for
    a real, permanent git write -- unlike `s03_phase_authorization`, which
    authorizes work confined to a disposable, isolated workspace."""

    decision = _policy().evaluate(
        _request(
            "apply_to_real_repo",
            {"phase_id": "phase-1", "case_id": "OPT-1", "base_revision": "a" * 40},
        )
    )

    assert decision.allowed is True
    assert decision.decision == "require_approval"


def test_every_decision_carries_the_pinned_policy_version() -> None:
    policy = _policy()
    requests = [
        _request("a2.execution_authorization", _execution_facts()),
        _request("a2.collector_authorization", {"collector_id": "c", "source_type": "test"}),
        _request("s03_phase_authorization", {"phase_id": "p", "case_id": "c"}),
        _request(
            "apply_to_real_repo",
            {"phase_id": "p", "case_id": "c", "base_revision": "a" * 40},
        ),
        _request("automatic_intake", {"opportunity_id": "OPP-1"}),
        _request("automatic_discovery_read", {"query_kind": "metrics"}),
        _request("unregistered.type", {}),
    ]

    for request in requests:
        assert policy.evaluate(request).policy_version == _POLICY_VERSION, request.decision_type
