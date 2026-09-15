# pyright: reportPrivateUsage=false
"""Implementation of business node B1.90."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.b1 import DetectionReport, RegisteredSourceSet
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_b1_90_construct_optimizationrequest_1_0_origin_automatic_verified(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    report = _read_model(ports, state, _require_ref(state, "DetectionReport"), DetectionReport)
    source_binding = report.source_bindings[0] if report.source_bindings else None
    top_signal = max(report.signals, key=lambda signal: signal.severity) if report.signals else None
    feature_binding = next(
        (
            binding
            for binding in report.feature_bindings
            if top_signal is not None and top_signal.signal_id in binding.signal_ids
        ),
        None,
    )
    feature_id = (
        feature_binding.feature_id
        if feature_binding is not None and feature_binding.feature_id is not None
        else _required_state_str(state, "case_id")
    )

    objective = Objective(
        statement=(
            top_signal.description
            if top_signal is not None
            else "Automatically detected optimization opportunity"
        ),
        feature_id=feature_id,
    )
    criterion = Criterion(
        criterion_id=f"auto-{top_signal.signal_id}" if top_signal is not None else "auto-criterion",
        metric_id=top_signal.metric_id if top_signal is not None else "unknown_metric",
        direction="minimize",
        target=(
            (
                top_signal.baseline_value
                if top_signal.baseline_value is not None
                else top_signal.observed_value
            )
            if top_signal is not None
            else 0.0
        ),
        unit=top_signal.unit if top_signal is not None else "unitless",
        weight=1.0,
    )
    workload = WorkloadContract(
        workload_id=f"auto-{feature_id}",
        environment_id="production",
        repetitions=3,
        warmup_runs=0,
        concurrency=1,
        cache_state="warm",
    )
    evidence_requirement = EvidenceRequirement(
        requirement_id=f"evidence-{criterion.criterion_id}",
        criterion_id=criterion.criterion_id,
        accepted_source_types={"benchmark", "test", "telemetry"},
        minimum_samples=3,
        mandatory=True,
    )
    budget = ExecutionBudget(
        deadline_seconds=3600,
        maximum_worker_seconds=180,
        maximum_model_tokens=100_000,
        maximum_storage_bytes=10_000_000,
    )
    fingerprint = sha256_digest(
        canonical_json(
            {
                "objective": objective.model_dump(mode="json"),
                "criteria": [criterion.model_dump(mode="json")],
                "workload": workload.model_dump(mode="json"),
            }
        )
    )
    approval = ApprovalBinding(
        approval_id=f"{_required_state_str(state, 'case_id')}-B1-APPROVAL",
        actor_id="system-discovery",
        actor_role="automatic",
        decision="approve",
        artifact_digest=fingerprint,
        policy_version="b1-intake-v1",
    )
    # `SourceBinding` (contracts/b1.py) carries `repository_id`/`git_revision`
    # but not a filesystem path -- `RegisteredSourceSet.sources[*].local_path`
    # is the only place that real path lives, so B1.90 reads it back rather
    # than sending `allowed_root_id="unknown"` at the embedded A2 (B1.95),
    # which would fail A2.20's real path-existence check.
    registry = _read_model(
        ports, state, _require_ref(state, "RegisteredSourceSet"), RegisteredSourceSet
    )
    matching_source = next(
        (
            candidate
            for candidate in registry.sources
            if source_binding is not None
            and candidate.repository_id == source_binding.repository_id
        ),
        registry.sources[0] if registry.sources else None,
    )
    local_root = matching_source.local_path if matching_source is not None else "unknown"
    source = SourceReference(
        repository_id=source_binding.repository_id if source_binding is not None else "unknown",
        allowed_root_id=local_root,
        relative_path=".",
        requested_revision=source_binding.git_revision if source_binding is not None else None,
    )
    request = _seal(
        OptimizationRequest(
            **_base_envelope(state, "OptimizationRequest", parents=[report.content_digest]),
            origin=Origin.AUTOMATIC,
            scope_profile=ScopeProfile.LOCAL_SANDBOX,
            source=source,
            objective=objective,
            criteria=[criterion],
            workload=workload,
            evidence_requirements=[evidence_requirement],
            budget=budget,
            approval=approval,
            request_fingerprint=fingerprint,
        )
    )
    ref = _put_envelope(ports, state, request, node_id="B1.90")
    return NodeExecution(updates={"artifact_refs": [ref], "request_ref": ref})


__all__ = ["handle_b1_90_construct_optimizationrequest_1_0_origin_automatic_verified"]
