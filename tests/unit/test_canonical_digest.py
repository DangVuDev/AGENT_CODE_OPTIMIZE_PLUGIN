from __future__ import annotations

from datetime import UTC, datetime, timedelta

from production_optimizer.contracts.a2 import TrustLevel
from production_optimizer.contracts.a3 import Finding, FindingJudgement, FindingSet
from production_optimizer.contracts.b1 import DetectionReport, DetectionSignal, RunGroup
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
    verify_model_digest,
)
from production_optimizer.contracts.envelope import ProducerIdentity


def _digest(character: str = "a") -> str:
    return f"sha256:{character * 64}"


def _producer() -> ProducerIdentity:
    return ProducerIdentity(name="digest-test", version="1.0.0")


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _finding_set() -> FindingSet:
    judgement = FindingJudgement(
        judge_id="JUDGE-1",
        finding_id="FIND-1",
        verdict="accept",
        reasons=["symptom and evidence are consistent"],
        model_id="judge-model",
        model_version="1.0",
    )
    finding = Finding(
        finding_id="FIND-1",
        problem_signal_ids=["SIG-1"],
        claim_type="observation",
        symptom="p95 latency exceeds guardrail",
        causal_claim="synchronous IO blocks the hot path",
        supporting_evidence_ids=["EVID-1"],
        confidence=0.6,
        trust_level=TrustLevel.T1,
        judgement=judgement,
    )
    return FindingSet(
        artifact_id="ART-FS-1",
        tenant_id="TENANT-1",
        case_id="OPT-1",
        created_at=_now(),
        producer=_producer(),
        content_digest=_digest("b"),
        evidence_bundle_digest=_digest("c"),
        findings=[finding],
    )


def _detection_report() -> DetectionReport:
    window_start = _now()
    window_end = window_start + timedelta(hours=1)
    run_group = RunGroup(
        group_id="GROUP-1",
        feature_id="FEATURE-1",
        workload_id="WORKLOAD-1",
        environment_id="ENV-1",
        sample_ids=["SAMPLE-1"],
        window_start=window_start,
        window_end=window_end,
        trust_level="T2",
    )
    signal = DetectionSignal(
        signal_id="SIG-1",
        signal_kind="regression",
        run_group_ids=["GROUP-1"],
        metric_id="p95_latency_ms",
        description="p95 latency regressed across comparable windows",
        observed_value=250.0,
        baseline_value=150.0,
        unit="ms",
        severity=0.8,
        detected_at=window_end,
    )
    return DetectionReport(
        artifact_id="ART-DR-1",
        tenant_id="TENANT-1",
        case_id="OPT-1",
        created_at=window_end,
        producer=_producer(),
        content_digest=_digest("d"),
        scan_id="SCAN-1",
        window_start=window_start,
        window_end=window_end,
        run_groups=[run_group],
        signals=[signal],
    )


def test_sha256_digest_and_canonical_json_round_trip() -> None:
    encoded = canonical_json({"b": 2, "a": 1})
    assert encoded == b'{"a":1,"b":2}'
    assert sha256_digest(encoded) == sha256_digest(canonical_json({"a": 1, "b": 2}))
    assert sha256_digest(encoded).startswith("sha256:")


def test_finding_set_content_digest_round_trip() -> None:
    finding_set = _finding_set()
    digest = model_content_digest(finding_set)
    sealed = finding_set.model_copy(update={"content_digest": digest})
    assert verify_model_digest(sealed) is True

    mutated = sealed.model_copy(update={"coverage_gaps": ["semantic analysis incomplete"]})
    assert verify_model_digest(mutated) is False


def test_detection_report_content_digest_round_trip() -> None:
    report = _detection_report()
    digest = model_content_digest(report)
    sealed = report.model_copy(update={"content_digest": digest})
    assert verify_model_digest(sealed) is True

    mutated = sealed.model_copy(update={"scan_id": "SCAN-2"})
    assert verify_model_digest(mutated) is False
