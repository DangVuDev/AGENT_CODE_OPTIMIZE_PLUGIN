# pyright: reportPrivateUsage=false
"""Implementation of business node A1.70."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import (
    ManualCasePayload,
    ProjectProfile,
    RawRequestDraft,
    WorkloadContract,
    WorkloadIdentity,
)
from production_optimizer.contracts.evaluation import ComposeExecutionContract
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _WORKLOAD_DEFAULTS_BY_LANGUAGE,
    _WORKLOAD_DEFAULTS_FALLBACK,
    _base_envelope,
    _logger,
    _most_common_language,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
    _workload_defaults_for_metric,
)


def handle_a1_70_resolve_repository_owned_workload_scenario_dataset_environment(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    profile = _read_model(ports, state, _require_ref(state, "ProjectProfile"), ProjectProfile)
    missing: list[str] = []
    if not draft.workload_id:
        missing.append("workload_id")
    if not draft.environment_id:
        missing.append("environment_id")
    workload: WorkloadContract | None = None
    execution: ComposeExecutionContract | None = None
    if not missing:
        # Data-driven instead of a flat "always 3 reps, 1 warmup": a Python
        # unit-test collection is fast and stable enough at 1 rep/0 warmup,
        # while JVM/.NET/JS runtimes and Go/Rust microbenchmarks genuinely
        # need warmup and repeats to be trustworthy (see
        # `A1_HONEST_ASSESSMENT.md` Problem 2.1 -- this used to cost every
        # Python case ~90s of avoidable collection time for no accuracy gain).
        detected_language = _most_common_language(profile.languages)
        metric_defaults = _workload_defaults_for_metric(draft.metric_id)
        language_defaults = _WORKLOAD_DEFAULTS_BY_LANGUAGE.get(
            detected_language, _WORKLOAD_DEFAULTS_FALLBACK
        )
        defaults = {
            "repetitions": max(metric_defaults["repetitions"], language_defaults["repetitions"]),
            "warmup_runs": max(metric_defaults["warmup_runs"], language_defaults["warmup_runs"]),
        }
        workload = WorkloadContract(
            workload_id=draft.workload_id or "",
            dataset_id=draft.dataset_id,
            environment_id=draft.environment_id or "",
            commands=draft.commands,
            repetitions=payload.repetitions or defaults["repetitions"],
            warmup_runs=(
                payload.warmup_runs if payload.warmup_runs is not None else defaults["warmup_runs"]
            ),
            concurrency=payload.concurrency or 1,
            cache_state=payload.cache_state or "warm",
        )
        if payload.execution_profile == "docker_compose":
            execution = ComposeExecutionContract(
                compose_file=payload.compose_file or "",
                application_services=payload.application_services,
                evaluations=payload.evaluations,
                startup_timeout_seconds=min(payload.maximum_worker_seconds or 180, 3600),
                cleanup_timeout_seconds=min(
                    max((payload.maximum_worker_seconds or 180) // 3, 1), 600
                ),
            )
        _logger.info(
            "A1.70: workload identity created",
            extra={
                "case_id": state.get("case_id"),
                "language": detected_language,
                "repetitions": workload.repetitions,
                "warmup_runs": workload.warmup_runs,
            },
        )
    artifact = _seal(
        WorkloadIdentity(
            **_base_envelope(
                state, "WorkloadIdentity", parents=[draft.content_digest, profile.content_digest]
            ),
            workload=workload,
            execution=execution,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.70")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_70_resolve_repository_owned_workload_scenario_dataset_environment"]
