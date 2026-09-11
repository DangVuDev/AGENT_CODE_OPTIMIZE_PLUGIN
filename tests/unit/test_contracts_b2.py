from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from production_optimizer.contracts.b2 import (
    AnalysisStrategy,
    DiscoveryAssumption,
    DiscoveryAssumptionReport,
    ModelContextPackage,
    ProposalApproval,
    ProposalEnvelope,
    ProposalRoutingDecision,
    StalenessDecision,
    TruncationReport,
)
from production_optimizer.contracts.envelope import ProducerIdentity


def _digest(character: str = "a") -> str:
    return f"sha256:{character * 64}"


def _producer() -> ProducerIdentity:
    return ProducerIdentity(name="b2-service", version="1.0.0")


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _routing_decision(
    route: Literal["auto_forward", "owner_review", "security_review", "reject"] = "auto_forward",
    required_actor_role: str | None = None,
) -> ProposalRoutingDecision:
    return ProposalRoutingDecision(
        decision_id="ROUTE-1",
        route=route,
        reasons=["confidence above auto-forward threshold"],
        policy_version="policy-v1",
        required_actor_role=required_actor_role,
    )


def _proposal_envelope_kwargs() -> dict[str, Any]:
    return {
        "artifact_id": "ART-PE-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _now(),
        "producer": _producer(),
        "content_digest": _digest("b"),
        "qualified_opportunity_digest": _digest("c"),
        "finding_set_digest": _digest("d"),
        "solution_portfolio_digest": _digest("e"),
        "quality_report_digest": _digest("f"),
        "routing_decision": _routing_decision(),
        "approval": ProposalApproval(decision="pending", policy_version="policy-v1"),
        "staleness": StalenessDecision(decision_id="STALE-1", stale=False, action="proceed"),
    }


def test_proposal_envelope_construction_and_pinned_fields() -> None:
    envelope = ProposalEnvelope(**_proposal_envelope_kwargs())
    assert envelope.artifact_type == "ProposalEnvelope"
    assert envelope.schema_version == "1.0"


def test_proposal_envelope_requires_routing_decision() -> None:
    kwargs = _proposal_envelope_kwargs()
    del kwargs["routing_decision"]
    with pytest.raises(ValidationError, match="routing_decision"):
        ProposalEnvelope(**kwargs)


def test_proposal_envelope_rejects_malformed_digest() -> None:
    kwargs = _proposal_envelope_kwargs()
    kwargs["solution_portfolio_digest"] = "bad"
    with pytest.raises(ValidationError, match="solution_portfolio_digest"):
        ProposalEnvelope(**kwargs)


def test_truncation_report_requires_reason_when_truncated() -> None:
    report = TruncationReport(truncated=False)
    assert report.truncated is False
    with pytest.raises(ValidationError, match="truncated context package requires a reason"):
        TruncationReport(truncated=True, reason=None)


def test_model_context_package_carries_truncation_report() -> None:
    package = ModelContextPackage(
        package_id="CTX-1",
        strategy_id="STRAT-1",
        evidence_ids=["EVID-1"],
        criteria_ids=["CRIT-1"],
        truncation_report=TruncationReport(
            truncated=True,
            omitted_evidence_ids=["EVID-2"],
            reason="evidence coverage limit reached",
        ),
    )
    assert package.truncation_report.truncated is True


def test_staleness_decision_action_must_match_staleness() -> None:
    fresh = StalenessDecision(decision_id="STALE-1", stale=False, action="proceed")
    assert fresh.action == "proceed"
    with pytest.raises(ValidationError, match="stale proposal cannot proceed"):
        StalenessDecision(decision_id="STALE-1", stale=True, action="proceed")
    with pytest.raises(ValidationError, match="fresh proposal must proceed"):
        StalenessDecision(decision_id="STALE-1", stale=False, action="refresh")


def test_proposal_routing_decision_review_route_requires_actor_role() -> None:
    decision = _routing_decision(route="owner_review", required_actor_role="repository_owner")
    assert decision.required_actor_role == "repository_owner"
    with pytest.raises(ValidationError, match="requires required_actor_role"):
        _routing_decision(route="owner_review", required_actor_role=None)


def test_discovery_assumption_report_construction() -> None:
    report = DiscoveryAssumptionReport(
        report_id="ASSUME-1",
        feature_binding_confirmed=True,
        source_binding_confirmed=True,
        owner_binding_confirmed=True,
        assumptions=[
            DiscoveryAssumption(
                assumption_id="ASM-1",
                statement="feature identity was resolved via explicit label",
                basis="detected_fact",
                verified=True,
            )
        ],
    )
    assert report.feature_binding_confirmed is True


def test_analysis_strategy_requires_selected_analyzers() -> None:
    with pytest.raises(ValidationError, match="selected_analyzers"):
        AnalysisStrategy(strategy_id="STRAT-1", selected_analyzers=[])
