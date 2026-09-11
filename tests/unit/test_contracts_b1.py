from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from production_optimizer.contracts.b1 import (
    CooldownDecision,
    DetectionReport,
    DetectionSignal,
    FeatureBinding,
    OwnershipBinding,
    QualificationDecision,
    QualifiedOpportunity,
    RunGroup,
    SourceBinding,
)
from production_optimizer.contracts.envelope import ProducerIdentity


def _digest(character: str = "a") -> str:
    return f"sha256:{character * 64}"


def _producer() -> ProducerIdentity:
    return ProducerIdentity(name="b1-service", version="1.0.0")


def _window_start() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _window_end() -> datetime:
    return _window_start() + timedelta(hours=1)


def _run_group() -> RunGroup:
    return RunGroup(
        group_id="GROUP-1",
        feature_id="FEATURE-1",
        workload_id="WORKLOAD-1",
        environment_id="ENV-1",
        sample_ids=["SAMPLE-1"],
        window_start=_window_start(),
        window_end=_window_end(),
        trust_level="T2",
    )


def _detection_signal() -> DetectionSignal:
    return DetectionSignal(
        signal_id="SIG-1",
        signal_kind="regression",
        run_group_ids=["GROUP-1"],
        metric_id="p95_latency_ms",
        description="p95 latency regressed across comparable windows",
        observed_value=250.0,
        baseline_value=150.0,
        unit="ms",
        severity=0.8,
        detected_at=_window_end(),
    )


def _source_binding(resolved: bool = True, unresolved_reason: str | None = None) -> SourceBinding:
    return SourceBinding(
        binding_id="SRC-BIND-1",
        repository_id="REPO-1",
        git_revision="deadbeef" if resolved else None,
        resolved=resolved,
        unresolved_reason=unresolved_reason,
    )


def _ownership_binding() -> OwnershipBinding:
    return OwnershipBinding(
        binding_id="OWN-BIND-1",
        code_owner="team-platform",
        service_owner="team-platform",
        decision_owner="team-platform",
        resolved=True,
    )


def _detection_report_kwargs() -> dict[str, Any]:
    return {
        "artifact_id": "ART-DR-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _window_end(),
        "producer": _producer(),
        "content_digest": _digest("b"),
        "scan_id": "SCAN-1",
        "window_start": _window_start(),
        "window_end": _window_end(),
        "run_groups": [_run_group()],
        "signals": [_detection_signal()],
    }


def _qualified_opportunity_kwargs() -> dict[str, Any]:
    return {
        "artifact_id": "ART-QO-1",
        "tenant_id": "TENANT-1",
        "case_id": "OPT-1",
        "created_at": _window_end(),
        "producer": _producer(),
        "content_digest": _digest("c"),
        "detection_report_digest": _digest("d"),
        "request_digest": _digest("e"),
        "source_snapshot_digest": _digest("f"),
        "baseline_digest": _digest("0"),
        "evidence_bundle_digest": _digest("1"),
        "comparability_report_digest": _digest("2"),
        "source_binding": _source_binding(),
        "owner_binding": _ownership_binding(),
        "case_start_id": "CASE-START-1",
    }


def test_detection_report_construction_and_pinned_fields() -> None:
    report = DetectionReport(**_detection_report_kwargs())
    assert report.artifact_type == "DetectionReport"
    assert report.schema_version == "1.0"


def test_detection_report_requires_scan_id() -> None:
    kwargs = _detection_report_kwargs()
    del kwargs["scan_id"]
    with pytest.raises(ValidationError, match="scan_id"):
        DetectionReport(**kwargs)


def test_detection_report_rejects_malformed_digest() -> None:
    kwargs = _detection_report_kwargs()
    kwargs["content_digest"] = "not-a-digest"
    with pytest.raises(ValidationError, match="content_digest"):
        DetectionReport(**kwargs)


def test_detection_report_window_end_must_follow_start() -> None:
    kwargs = _detection_report_kwargs()
    report = DetectionReport(**kwargs)
    assert report.window_end > report.window_start
    kwargs["window_end"] = kwargs["window_start"]
    with pytest.raises(ValidationError, match="window_end must be after window_start"):
        DetectionReport(**kwargs)


def test_qualified_opportunity_construction_and_pinned_fields() -> None:
    opportunity = QualifiedOpportunity(**_qualified_opportunity_kwargs())
    assert opportunity.artifact_type == "QualifiedOpportunity"
    assert opportunity.schema_version == "1.0"


def test_qualified_opportunity_requires_case_start_id() -> None:
    kwargs = _qualified_opportunity_kwargs()
    del kwargs["case_start_id"]
    with pytest.raises(ValidationError, match="case_start_id"):
        QualifiedOpportunity(**kwargs)


def test_qualified_opportunity_rejects_malformed_digest() -> None:
    kwargs = _qualified_opportunity_kwargs()
    kwargs["baseline_digest"] = "bad"
    with pytest.raises(ValidationError, match="baseline_digest"):
        QualifiedOpportunity(**kwargs)


def test_run_group_window_end_must_follow_start() -> None:
    group = _run_group()
    assert group.window_end > group.window_start
    with pytest.raises(ValidationError, match="window_end must be after window_start"):
        RunGroup(
            group_id="GROUP-1",
            feature_id="FEATURE-1",
            workload_id="WORKLOAD-1",
            environment_id="ENV-1",
            sample_ids=["SAMPLE-1"],
            window_start=_window_end(),
            window_end=_window_start(),
            trust_level="T2",
        )


def test_feature_binding_resolved_requires_feature_id() -> None:
    binding = FeatureBinding(
        binding_id="FB-1",
        signal_ids=["SIG-1"],
        feature_id="FEATURE-1",
        confidence=0.9,
        method="explicit_label",
        resolved=True,
    )
    assert binding.resolved is True
    with pytest.raises(ValidationError, match="must include a feature_id"):
        FeatureBinding(
            binding_id="FB-1",
            signal_ids=["SIG-1"],
            feature_id=None,
            confidence=0.9,
            method="explicit_label",
            resolved=True,
        )


def test_source_binding_unresolved_requires_reason() -> None:
    resolved = _source_binding(resolved=True)
    assert resolved.resolved is True
    with pytest.raises(ValidationError, match="unresolved_reason"):
        _source_binding(resolved=False, unresolved_reason=None)


def test_qualification_decision_requires_reasons_when_disqualified() -> None:
    decision = QualificationDecision(
        decision_id="QUAL-1", qualified=True, policy_version="policy-v1"
    )
    assert decision.qualified is True
    with pytest.raises(ValidationError, match="disqualified opportunity must include reasons"):
        QualificationDecision(
            decision_id="QUAL-1", qualified=False, reasons=[], policy_version="policy-v1"
        )


def test_cooldown_decision_suppressed_requires_reason() -> None:
    decision = CooldownDecision(decision_id="COOL-1", suppressed=False)
    assert decision.suppressed is False
    with pytest.raises(ValidationError, match="suppressed candidate requires a reason"):
        CooldownDecision(decision_id="COOL-1", suppressed=True, reason=None)
