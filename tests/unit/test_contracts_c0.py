from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from production_optimizer.contracts.c0 import (
    ConvergedCase,
    ConvergenceDecision,
    DigestChainLink,
    DimensionEquivalenceVerdict,
    FreshnessCheck,
    SchemaValidationResult,
)
from production_optimizer.contracts.envelope import ProducerIdentity


def _digest(character: str = "a") -> str:
    return f"sha256:{character * 64}"


def _producer() -> ProducerIdentity:
    return ProducerIdentity(name="c0-service", version="1.0.0")


def _checked_at() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _passing_schema_result() -> SchemaValidationResult:
    return SchemaValidationResult(
        artifact_type="OptimizationRequest", schema_version="1.0", valid=True
    )


def _passing_digest_link() -> DigestChainLink:
    return DigestChainLink(
        artifact_type="OptimizationRequest", artifact_digest=_digest("b"), linked=True
    )


def _passing_equivalence_verdict() -> DimensionEquivalenceVerdict:
    return DimensionEquivalenceVerdict(dimension="workload_identity", equivalent=True)


def _passing_freshness_check() -> FreshnessCheck:
    return FreshnessCheck(dimension="source_snapshot", fresh=True, checked_at=_checked_at())


def _convergence_decision_kwargs(*, converged: bool = True) -> dict[str, Any]:
    return {
        "artifact_id": "ART-CD-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _checked_at(),
        "producer": _producer(),
        "content_digest": _digest("c"),
        "origin": "manual",
        "schema_results": [_passing_schema_result()],
        "digest_chain": [_passing_digest_link()],
        "equivalence_verdicts": [_passing_equivalence_verdict()],
        "freshness_checks": [_passing_freshness_check()],
        "eligible_solution_count": 1,
        "converged": converged,
    }


def _converged_case_kwargs() -> dict[str, Any]:
    return {
        "artifact_id": "ART-CC-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _checked_at(),
        "producer": _producer(),
        "content_digest": _digest("d"),
        "origin": "automatic",
        "request_digest": _digest("e"),
        "source_snapshot_digest": _digest("f"),
        "baseline_digest": _digest("0"),
        "evidence_bundle_digest": _digest("1"),
        "solution_portfolio_digest": _digest("2"),
        "convergence_decision_digest": _digest("3"),
    }


def test_convergence_decision_construction_and_pinned_fields() -> None:
    decision = ConvergenceDecision(**_convergence_decision_kwargs())
    assert decision.artifact_type == "ConvergenceDecision"
    assert decision.schema_version == "1.0"
    assert decision.converged is True


def test_convergence_decision_requires_schema_results() -> None:
    kwargs = _convergence_decision_kwargs()
    del kwargs["schema_results"]
    with pytest.raises(ValidationError, match="schema_results"):
        ConvergenceDecision(**kwargs)


def test_convergence_decision_rejects_malformed_digest() -> None:
    kwargs = _convergence_decision_kwargs()
    kwargs["content_digest"] = "not-a-digest"
    with pytest.raises(ValidationError, match="content_digest"):
        ConvergenceDecision(**kwargs)


def test_convergence_decision_gate_requires_all_checks_to_pass() -> None:
    kwargs = _convergence_decision_kwargs(converged=True)
    kwargs["freshness_checks"] = [
        FreshnessCheck(dimension="source_snapshot", fresh=False, checked_at=_checked_at())
    ]
    with pytest.raises(ValidationError, match="converged must reflect"):
        ConvergenceDecision(**kwargs)


def test_convergence_decision_gate_requires_at_least_one_eligible_solution() -> None:
    kwargs = _convergence_decision_kwargs(converged=True)
    kwargs["eligible_solution_count"] = 0
    with pytest.raises(ValidationError, match="converged must reflect"):
        ConvergenceDecision(**kwargs)


def test_convergence_decision_failed_gate_requires_reasons() -> None:
    kwargs = _convergence_decision_kwargs(converged=False)
    kwargs["eligible_solution_count"] = 0
    kwargs["reasons"] = []
    with pytest.raises(ValidationError, match="failed convergence decision requires reasons"):
        ConvergenceDecision(**kwargs)
    kwargs["reasons"] = ["no eligible solution in portfolio"]
    decision = ConvergenceDecision(**kwargs)
    assert decision.converged is False


def test_converged_case_construction_and_pinned_fields() -> None:
    case = ConvergedCase(**_converged_case_kwargs())
    assert case.artifact_type == "ConvergedCase"
    assert case.schema_version == "1.0"


def test_converged_case_requires_request_digest() -> None:
    kwargs = _converged_case_kwargs()
    del kwargs["request_digest"]
    with pytest.raises(ValidationError, match="request_digest"):
        ConvergedCase(**kwargs)


def test_converged_case_rejects_malformed_digest() -> None:
    kwargs = _converged_case_kwargs()
    kwargs["baseline_digest"] = "bad"
    with pytest.raises(ValidationError, match="baseline_digest"):
        ConvergedCase(**kwargs)


def test_schema_validation_result_invalid_requires_errors() -> None:
    valid_result = SchemaValidationResult(
        artifact_type="OptimizationRequest", schema_version="1.0", valid=True
    )
    assert valid_result.valid is True
    with pytest.raises(ValidationError, match="invalid schema result requires errors"):
        SchemaValidationResult(
            artifact_type="OptimizationRequest", schema_version="1.0", valid=False, errors=[]
        )


def test_dimension_equivalence_verdict_requires_reason_when_not_equivalent() -> None:
    equivalent = DimensionEquivalenceVerdict(dimension="workload_identity", equivalent=True)
    assert equivalent.equivalent is True
    with pytest.raises(ValidationError, match="non-equivalent dimension requires a reason"):
        DimensionEquivalenceVerdict(dimension="workload_identity", equivalent=False, reason=None)


def test_freshness_check_expiry_must_follow_checked_at() -> None:
    check = FreshnessCheck(
        dimension="approval",
        fresh=True,
        checked_at=_checked_at(),
        expires_at=_checked_at() + timedelta(minutes=15),
    )
    assert check.expires_at is not None and check.expires_at > check.checked_at
    with pytest.raises(ValidationError, match="expires_at must be after checked_at"):
        FreshnessCheck(
            dimension="approval",
            fresh=True,
            checked_at=_checked_at(),
            expires_at=_checked_at(),
        )
