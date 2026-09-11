from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from production_optimizer.application import NodeSpec, SideEffectClass
from production_optimizer.contracts import ArtifactRef, InterruptEnvelope
from production_optimizer.contracts.errors import ErrorCode
from production_optimizer.contracts.state import OptimizationState, merge_artifact_refs


def artifact_ref(*, artifact_id: str = "ART-1", digest_character: str = "a") -> ArtifactRef:
    return ArtifactRef(
        artifact_type="BootstrapArtifact",
        schema_version="1.0",
        artifact_id=artifact_id,
        content_digest=f"sha256:{digest_character * 64}",
        uri=f"s3://development/{artifact_id}",
    )


def test_state_contract_contains_references_not_raw_payloads() -> None:
    fields = set(OptimizationState.__annotations__)
    forbidden = {"source", "raw_source", "evidence", "raw_evidence", "secrets", "transcript"}
    assert not fields & forbidden
    assert {"request_ref", "baseline_ref", "solution_portfolio_ref"} <= fields


def test_artifact_reducer_is_deterministic_and_deduplicates() -> None:
    first = artifact_ref(artifact_id="ART-2")
    second = artifact_ref(artifact_id="ART-1")
    result = merge_artifact_refs([first], [second, first])
    assert [item.artifact_id for item in result] == ["ART-1", "ART-2"]


def test_artifact_reducer_rejects_same_identity_with_different_content() -> None:
    with pytest.raises(ValueError, match="artifact reference conflict"):
        merge_artifact_refs(
            [artifact_ref(digest_character="a")],
            [artifact_ref(digest_character="b")],
        )


def test_interrupt_requires_an_expiring_digest_bound_decision() -> None:
    issued_at = datetime.now(UTC)
    interrupt = InterruptEnvelope(
        interrupt_id="INT-1",
        case_id="OPT-1",
        thread_id="THREAD-1",
        stage="BOOTSTRAP_SMOKE",
        artifact_digest=f"sha256:{'a' * 64}",
        allowed_decisions=["approve", "reject"],
        required_actor_role="platform_owner",
        policy_version="bootstrap-v1",
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=15),
    )
    assert interrupt.expires_at > interrupt.issued_at

    invalid = interrupt.model_dump()
    invalid["expires_at"] = issued_at
    with pytest.raises(ValidationError, match="expiration must follow"):
        InterruptEnvelope.model_validate(invalid)


def test_pure_node_contract_cannot_define_internal_retries() -> None:
    with pytest.raises(ValidationError, match="must be recomputed"):
        NodeSpec(
            node_id="future-node",
            business_task_id="future.task",
            owner="control-plane",
            input_contract="Input@1.0",
            output_contract="Output@1.0",
            supported_schema_majors={1},
            idempotency_key_version="1",
            side_effect_class=SideEffectClass.PURE,
            timeout_seconds=5,
            max_attempts=2,
            retryable_errors={ErrorCode.INTERNAL_ERROR},
            allowed_routes={"complete"},
            runbook="docs/runbooks/future.md",
            slo="p95 < 1s",
        )
