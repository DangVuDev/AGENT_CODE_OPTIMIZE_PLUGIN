# pyright: reportPrivateUsage=false
"""Exercises A3's pure evidence/validation-command helpers directly --
these decide grounding/coverage classification and deserve their own tests
rather than only being reachable through a full A3 pipeline run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from production_optimizer.application.a3_handlers.nodes.a3_20_deterministically_compare_baseline_criteria_guardrails_distributions_fir import (  # noqa: E501
    _grounding_suffix,
)
from production_optimizer.application.a3_handlers.shared import (
    _evidence_supports_claim,
    _validation_command_candidates,
)
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Guardrail,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.a2 import RepositoryCommand, VerificationManifest
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.evaluation import (
    ComposeExecutionContract,
    ContainerCommandSpec,
    EvaluationSpec,
)

_ZERO_DIGEST = "sha256:" + "0" * 64
_TEST_PRODUCER = ProducerIdentity(name="test", version="1.0")
_TENANT_ID = "TENANT-A3-HELPERS"
_CASE_ID = "OPT-A3-HELPERS-1"


@dataclass
class _StubEvidenceItem:
    """A minimal stand-in for `EvidenceItem` -- `_evidence_supports_claim`
    only ever reads `.value` via `getattr`, so a full Pydantic-validated
    `EvidenceItem` (with its own `EvidenceIdentity`/`ArtifactRef`
    dependencies) would be unnecessary ceremony for this pure predicate."""

    value: object


def test_evidence_supports_claim_treats_a_real_zero_as_supporting() -> None:
    """A real zero value (e.g. a guardrail metric genuinely at 0, like
    `error_budget_remaining=0`) is just as valid a supporting value as any
    nonzero one -- the falsy-zero bug this guards against previously
    excluded it via a `value != 0` clause."""

    assert _evidence_supports_claim(_StubEvidenceItem(value=0)) is True
    assert _evidence_supports_claim(_StubEvidenceItem(value=0.0)) is True


def test_evidence_supports_claim_still_rejects_non_numeric_or_missing_values() -> None:
    assert _evidence_supports_claim(_StubEvidenceItem(value="unmeasured")) is False
    assert _evidence_supports_claim(_StubEvidenceItem(value=None)) is False
    assert _evidence_supports_claim(_StubEvidenceItem(value=True)) is False


def test_grounding_suffix_is_empty_for_precise_numeric_evidence() -> None:
    precise_ids = ["evidence-1"]
    assert _grounding_suffix(precise_ids, precise_ids=precise_ids) == ""


def test_grounding_suffix_labels_weak_same_metric_fallback() -> None:
    """When the precise numeric match found nothing (e.g. the only evidence
    for this metric has a non-numeric value like "unmeasured") but a
    same-metric fallback still produced evidence_ids, that must be labeled
    as unconfirmed rather than looking indistinguishable from a precise
    match."""

    precise_ids: list[str] = []
    fallback_ids = ["evidence-from-catalog"]
    assert (
        _grounding_suffix(fallback_ids, precise_ids=precise_ids)
        == " [evidence: same-metric, not numerically confirmed]"
    )


def test_grounding_suffix_labels_no_evidence_available() -> None:
    assert _grounding_suffix([], precise_ids=[]) == " [evidence: none available]"


def _build_request(
    *, execution: ComposeExecutionContract | None, guardrails: list[Guardrail]
) -> OptimizationRequest:
    return OptimizationRequest(
        artifact_id="request-1",
        tenant_id=_TENANT_ID,
        case_id=_CASE_ID,
        created_at=datetime.now(UTC),
        producer=_TEST_PRODUCER,
        origin=Origin.MANUAL,
        scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="repo-123",
            allowed_root_id="root-1",
            relative_path=".",
            requested_revision=None,
        ),
        objective=Objective(statement="fix failing tests", feature_id="local-feature"),
        criteria=[
            Criterion(
                criterion_id="primary",
                metric_id="p95_latency_ms",
                direction="minimize",
                target=100.0,
                unit="ms",
                weight=1.0,
            )
        ],
        guardrails=guardrails,
        workload=WorkloadContract(
            workload_id="local-workload",
            environment_id="local-env",
            repetitions=1,
            warmup_runs=0,
            concurrency=1,
            cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="evidence-primary",
                criterion_id="primary",
                accepted_source_types={"benchmark"},
                minimum_samples=1,
                mandatory=True,
                metric_id="p95_latency_ms",
                canonical_unit="ms",
                aggregation="mean",
            )
        ],
        budget=ExecutionBudget(
            deadline_seconds=300,
            maximum_worker_seconds=180,
            maximum_model_tokens=100_000,
            maximum_storage_bytes=50_000_000,
        ),
        approval=ApprovalBinding(
            approval_id="approval-1",
            actor_id="actor-1",
            actor_role="owner",
            decision="approve",
            artifact_digest=_ZERO_DIGEST,
            policy_version="test-v1",
        ),
        request_fingerprint=_ZERO_DIGEST,
        content_digest=_ZERO_DIGEST,
        execution=execution,
    )


def test_validation_command_candidates_merges_metric_ids_on_command_id_collision() -> None:
    """A compose evaluation and a repository-owned command that happen to
    share a command_id/evaluation_id must not silently drop one source's
    metric_ids -- the union of both is kept instead of whichever source
    was appended first winning outright."""

    request = _build_request(
        execution=ComposeExecutionContract(
            compose_file="compose.yaml",
            application_services=["app"],
            evaluations=[
                EvaluationSpec(
                    evaluation_id="shared-check",
                    command=ContainerCommandSpec(service="app", argv=["run.sh"]),
                    expected_metric_ids={"p95_latency_ms"},
                )
            ],
        ),
        guardrails=[
            Guardrail(
                guardrail_id="guard-1",
                metric_id="correctness",
                operator="gte",
                threshold=1.0,
                unit="exit_code",
            )
        ],
    )
    verification = VerificationManifest(
        artifact_id="verification-1",
        tenant_id=_TENANT_ID,
        case_id=_CASE_ID,
        created_at=datetime.now(UTC),
        producer=_TEST_PRODUCER,
        content_digest=_ZERO_DIGEST,
        commands=[
            RepositoryCommand(
                command_id="shared-check",
                argv=["pytest", "tests"],
                working_directory=".",
                kind="unit",
                source="pyproject_toml",
            )
        ],
    )

    candidates = _validation_command_candidates(request, verification)

    assert len(candidates) == 1
    candidate = candidates[0]
    # Compose's curated metric id and the repository command's blanket
    # request-wide metric ids (every criterion + guardrail metric_id) are
    # both preserved, not one silently discarded.
    assert "p95_latency_ms" in candidate.metric_ids
    assert "correctness" in candidate.metric_ids
    assert candidate.source == "compose_evaluation"


def test_validation_command_candidates_keeps_distinct_ids_separate() -> None:
    request = _build_request(execution=None, guardrails=[])
    verification = VerificationManifest(
        artifact_id="verification-2",
        tenant_id=_TENANT_ID,
        case_id=_CASE_ID,
        created_at=datetime.now(UTC),
        producer=_TEST_PRODUCER,
        content_digest=_ZERO_DIGEST,
        commands=[
            RepositoryCommand(
                command_id="unit-1",
                argv=["pytest", "tests"],
                working_directory=".",
                kind="unit",
                source="pyproject_toml",
            )
        ],
    )

    candidates = _validation_command_candidates(request, verification)

    assert len(candidates) == 1
    assert candidates[0].command_id == "unit-1"
    assert candidates[0].source == "repository_command"
