# pyright: reportPrivateUsage=false
"""Implementation of business node A2.90."""

from __future__ import annotations

from datetime import UTC, datetime

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    BranchEvidenceRefs,
    EvidenceBundle,
    EvidenceQualityReport,
    TrustLevel,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _FAN_IN_BRANCH_IDS,
    _REDACTION_KEYWORDS,
    _TRUST_RANK,
    _base_envelope,
    _effective_minimum_samples,
    _freshness_limit,
    _put_envelope,
    _read_model,
    _require_branch_ref,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_a2_90_evaluate_coverage_samples_freshness_integrity_redaction_failures(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Evaluate coverage, freshness, integrity and redaction over the bundle.

    Every check reads real state: `mandatory_coverage`/`sample_failures` from
    actual evidence counts against `EvidenceRequirement.minimum_samples`;
    `integrity_failures` from `ArtifactStore.verify` (real digest
    recomputation, not a trust-me flag); `redaction_failures` from a literal
    keyword scan of stored command stdout/stderr; `collector_failures` from
    the `unavailable_reason` A2.62/A2.63 already recorded honestly. With the
    pilot's single-sample-per-command collection, `passed` is realistically
    `False` whenever `minimum_samples > 1` — that is the check working, not
    a bug.
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    branches = [
        _read_model(ports, state, _require_branch_ref(state, node_id), BranchEvidenceRefs)
        for node_id in _FAN_IN_BRANCH_IDS
    ]
    tenant_id = _required_state_str(state, "tenant_id")

    mandatory_coverage: dict[str, bool] = {}
    sample_failures: list[str] = []
    trust_failures: list[str] = []
    for requirement in request.evidence_requirements:
        matching = [
            item
            for item in bundle.evidence
            if item.requirement_id == requirement.requirement_id
            and (requirement.metric_id is None or item.metric_id == requirement.metric_id)
            and (requirement.canonical_unit is None or item.unit == requirement.canonical_unit)
            and requirement.required_dimensions <= set(item.dimensions)
        ]
        required = _effective_minimum_samples(requirement, matching)
        minimum_trust = {
            "unverified": TrustLevel.T0,
            "verified": TrustLevel.T2,
            "attested": TrustLevel.T3,
        }[requirement.minimum_trust_level]
        eligible = [
            item for item in matching if _TRUST_RANK[item.trust_level] >= _TRUST_RANK[minimum_trust]
        ]
        if len(eligible) != len(matching):
            trust_failures.append(
                f"{requirement.requirement_id}: {len(matching) - len(eligible)} "
                f"sample(s) below {minimum_trust}"
            )
        satisfied = len(eligible) >= required
        if requirement.mandatory:
            mandatory_coverage[requirement.requirement_id] = satisfied
        if not satisfied:
            sample_failures.append(
                f"{requirement.requirement_id}: collected {len(matching)}, needs {required}"
            )

    now = datetime.now(UTC)
    freshness_failures = [
        f"{item.evidence_id}: observed {item.identity.observed_at.isoformat()}"
        for item in bundle.evidence
        if (now - item.identity.observed_at).total_seconds() > _freshness_limit(request, item)
    ]

    integrity_failures = [
        item.evidence_id
        for item in bundle.evidence
        if not ports.artifacts.verify(tenant_id=tenant_id, ref=item.raw_ref)
        or item.normalized_ref is None
        or not ports.artifacts.verify(tenant_id=tenant_id, ref=item.normalized_ref)
    ]

    redaction_failures: list[str] = []
    for item in bundle.evidence:
        raw_bytes = ports.artifacts.read(tenant_id=tenant_id, ref=item.raw_ref)
        lowered = raw_bytes.decode("utf-8", errors="ignore").lower()
        if any(keyword in lowered for keyword in _REDACTION_KEYWORDS):
            redaction_failures.append(item.evidence_id)

    collector_failures = {
        branch.branch_id: branch.unavailable_reason
        for branch in branches
        if branch.unavailable_reason
    }

    mandatory_satisfied = all(mandatory_coverage.values())
    passed = (
        mandatory_satisfied
        and not sample_failures
        and not trust_failures
        and not freshness_failures
        and not integrity_failures
        and not redaction_failures
    )

    # "missing" when a mandatory requirement has too little evidence to judge
    # at all; "rejected" when evidence exists but failed a hard check
    # (integrity/redaction) or went stale — matches the route vocabulary
    # `orchestration/subgraphs/a2.py` compiles for this node.
    if passed:
        route = NodeRoute.CONTINUE
    elif not mandatory_satisfied:
        route = NodeRoute.MISSING
    else:
        route = NodeRoute.REJECTED

    report = _seal(
        EvidenceQualityReport(
            **_base_envelope(state, "EvidenceQualityReport", parents=[bundle.content_digest]),
            passed=passed,
            mandatory_coverage=mandatory_coverage,
            sample_failures=sample_failures,
            trust_failures=trust_failures,
            freshness_failures=freshness_failures,
            integrity_failures=integrity_failures,
            redaction_failures=redaction_failures,
            collector_failures=collector_failures,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="A2.90")
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_90_evaluate_coverage_samples_freshness_integrity_redaction_failures"]
