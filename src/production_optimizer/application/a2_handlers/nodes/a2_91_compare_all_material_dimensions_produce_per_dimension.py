# pyright: reportPrivateUsage=false
"""Implementation of business node A2.91."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts, NodeRoute
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    ComparabilityReport,
    DimensionVerdict,
    EnvironmentManifest,
    EvidenceBundle,
    SourceSnapshot,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a2_91_compare_all_material_dimensions_produce_per_dimension(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Verdict per material dimension between the request and what was observed.

    Only two dimensions are checked, and only because both are honestly
    verifiable from data this pilot actually has: `cache_state` (A2.41
    copies it straight from the request, so a mismatch would mean a real
    bug) and `workload_identity` (every evidence item's
    `identity.workload_id`/`environment_id` must match the request). A wider
    comparability check (hardware class, dataset version, ...) needs
    evidence this platform does not collect yet and is deliberately not
    asserted here rather than guessed.
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    env = _read_model(ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)

    dimensions: list[DimensionVerdict] = []

    cache_match = env.cache_state == request.workload.cache_state
    dimensions.append(
        DimensionVerdict(
            dimension="cache_state",
            comparable=cache_match,
            baseline_value=env.cache_state,
            expected_value=request.workload.cache_state,
            material=True,
            reason="environment capture must match the requested workload cache state",
        )
    )

    source_mismatches = sorted(
        item.evidence_id
        for item in bundle.evidence
        if item.identity.source_snapshot_digest != snapshot.content_digest
        or item.identity.repository_id != snapshot.repository_id
    )
    dimensions.append(
        DimensionVerdict(
            dimension="source_snapshot",
            comparable=not source_mismatches,
            baseline_value=snapshot.content_digest,
            expected_value=snapshot.content_digest,
            material=True,
            reason=(
                "all evidence is bound to the immutable source snapshot"
                if not source_mismatches
                else f"mismatched evidence: {source_mismatches}"
            ),
        )
    )

    concurrency_mismatches = sorted(
        item.evidence_id
        for item in bundle.evidence
        if item.identity.concurrency != request.workload.concurrency
    )
    dimensions.append(
        DimensionVerdict(
            dimension="concurrency",
            comparable=not concurrency_mismatches,
            baseline_value=str(env.concurrency),
            expected_value=str(request.workload.concurrency),
            material=True,
            reason=(
                "captured concurrency matches the workload contract"
                if not concurrency_mismatches
                else f"mismatched evidence: {concurrency_mismatches}"
            ),
        )
    )

    expected_dataset = request.workload.dataset_id or "none"
    dataset_mismatches = sorted(
        item.evidence_id
        for item in bundle.evidence
        if (item.identity.dataset_id or "none") != expected_dataset
    )
    dimensions.append(
        DimensionVerdict(
            dimension="dataset_identity",
            comparable=not dataset_mismatches,
            baseline_value=expected_dataset,
            expected_value=expected_dataset,
            material=request.workload.dataset_id is not None,
            reason=(
                "dataset identity matches the workload contract"
                if not dataset_mismatches
                else f"mismatched evidence: {dataset_mismatches}"
            ),
        )
    )

    mismatched = sorted(
        item.evidence_id
        for item in bundle.evidence
        if item.identity.workload_id != request.workload.workload_id
        or item.identity.environment_id != request.workload.environment_id
    )
    identity_match = not mismatched
    dimensions.append(
        DimensionVerdict(
            dimension="workload_identity",
            comparable=identity_match,
            baseline_value=f"{request.workload.workload_id}/{request.workload.environment_id}",
            expected_value=f"{request.workload.workload_id}/{request.workload.environment_id}",
            material=True,
            reason=(
                "all evidence matched the requested workload/environment identity"
                if identity_match
                else f"mismatched evidence: {mismatched}"
            ),
        )
    )

    policy_version = request.approval.policy_version if request.approval else "unknown"
    comparable = all(dimension.comparable for dimension in dimensions if dimension.material)

    report = _seal(
        ComparabilityReport(
            **_base_envelope(state, "ComparabilityReport", parents=[bundle.content_digest]),
            comparable=comparable,
            dimensions=dimensions,
            policy_version=policy_version,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="A2.91")
    route = NodeRoute.CONTINUE if comparable else NodeRoute.INCOMPARABLE
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_91_compare_all_material_dimensions_produce_per_dimension"]
