# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
from __future__ import annotations

import ast
import io
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from production_optimizer.application.a2_worker_capabilities import BENCHMARK_JSON_FILENAME
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
)
from production_optimizer.contracts.a1 import EvidenceRequirement, OptimizationRequest
from production_optimizer.contracts.a2 import (
    A2IntakeDecision,
    BaselineSnapshot,
    BranchEvidenceRefs,
    CollectorBinding,
    CollectorPlan,
    ComparabilityReport,
    DimensionVerdict,
    EnvironmentManifest,
    EvidenceBundle,
    EvidenceIdentity,
    EvidenceItem,
    EvidenceQualityReport,
    ExecutionAuthorization,
    FileIdentity,
    MetricAggregate,
    NormalizedEvidenceSet,
    RawEvidenceFanIn,
    RepositoryCommand,
    RepositoryManifest,
    SourceAcquisitionRequest,
    SourceSnapshot,
    TrustLevel,
    VerificationManifest,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.evaluation import ComposeEvaluationWorkerResult
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelMessage,
    ModelRole,
    PolicyRequest,
    WorkerJob,
)
from production_optimizer.contracts.registries import EvidenceRecipeRegistration, RegistryKind
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="a2-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_DISALLOWED_PATHS = {
    ".git",
    ".env",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
}
_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
}
_MANIFEST_FILE_NAMES = {"pyproject.toml", "package.json", "go.mod", "Cargo.toml"}

# Compatibility recipes for local repository commands. A2.40 resolves the
# tenant's versioned registry first; this table is used only when a request
# explicitly asks for one of the built-in adapters.
_COLLECTOR_CATALOG_VERSION = "a2-static-collector-catalog-v1"
_COLLECTOR_CATALOG: dict[str, str] = {
    "test": "local-pytest-runner",
    "benchmark": "local-benchmark-runner",
    "telemetry": "otel-local-collector",
    "source_map": "a2-ast-source-mapper",
}

# A2.60/A2.61/A2.62 share one implementation (`_run_command_evidence_branch`):
# real WorkerBroker-backed execution of the repository-owned commands A2.31
# detected and A2.50 authorized, via `a2_worker_capabilities.py`. A2.62
# (benchmark) and A2.63 (telemetry) currently always report
# `unavailable_reason` — A2.31 never emits a `benchmark` command and no
# telemetry query adapter exists — which is an honest "no data" outcome the
# contract's `unavailable_reason` field exists for, not a stub. A2.64
# (source map) needs no external port at all: it parses Python source with
# stdlib `ast`. See ADR-0001 (`docs/adr/0001-registry-port-and-node-ports-
# expansion.md`) for the `NodePorts` wiring that unblocked A2.50 first.
# A2.71/A2.80/A2.90/A2.91/A2.95 close the lane: normalize -> bind provenance
# -> evaluate quality -> compare dimensions -> aggregate into
# `BaselineSnapshot`, all deterministic over whatever A2.60-A2.64 actually
# collected (see each handler's docstring for the specific judgment calls,
# e.g. `EvidenceBundle.baseline_digest`'s meaning, made where the contract
# leaves a value underspecified). All 18 A2 nodes are registered; this dict
# is kept (now empty) as the stable place a future lane-1 gap gets recorded,
# matching the pattern `A2_NODE_IDS` already relies on for validation.


# Must match the conditional edges `orchestration/subgraphs/a2.py` compiles
# for these two nodes. A2.90/A2.91 are the only A2 nodes the graph can route
# away from A2.95 on, so their handlers must be able to emit those routes —
# see `_a2_90`/`_a2_91`.


_A2_COMMAND_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "kind": {
            "type": "string",
            "enum": ["build", "unit", "integration", "benchmark", "lint", "type", "security"],
        },
    },
    "required": ["argv", "kind"],
}
_A2_MODEL_PROMPT_VERSION = "a2-command-proposal-v1"
_DEFAULT_A2_MODEL_ID = "claude-sonnet-5"


def _propose_command_via_llm(
    ports: NodePorts,
    state: OptimizationState,
    snapshot: SourceSnapshot,
    manifest: RepositoryManifest,
) -> dict[str, Any] | None:
    """Ask the model what command would run this repo's tests -- never trusted
    to run unattended: the caller always routes this to human approval."""

    assert ports.model is not None
    case_id = _required_state_str(state, "case_id")
    context = (
        f"Repository languages: {manifest.languages}\n"
        f"Manifest files: {manifest.manifest_files}\n"
        f"Test roots: {manifest.test_roots}\n"
        f"Top-level modules: {manifest.modules}\n"
        "No pyproject.toml convention and no CI config step matched a known "
        "tool. Propose ONE command to run this repository's test suite."
    )
    request = ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id=ports.model_id or _DEFAULT_A2_MODEL_ID,
        prompt_version=_A2_MODEL_PROMPT_VERSION,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You propose a single shell command to run a repository's test "
                    "suite. Only propose a command realistic for the languages and "
                    "manifest files actually listed. Never invent a tool, file, or "
                    "convention not implied by what is given."
                ),
            ),
            ModelMessage(role="user", content=context),
        ],
        response_schema=_A2_COMMAND_PROPOSAL_SCHEMA,
        max_output_tokens=300,
        idempotency_key=f"{case_id}:A2.31:{_A2_MODEL_PROMPT_VERSION}",
    )
    result = ports.model.complete(request)
    if not result.valid_json or result.parsed_json is None:
        return None

    payload = result.parsed_json
    argv = payload.get("argv")
    kind = payload.get("kind")
    valid_kinds = {"build", "unit", "integration", "benchmark", "lint", "type", "security"}
    if not isinstance(argv, list) or not argv or kind not in valid_kinds:
        return None
    if not all(isinstance(token, str) for token in cast("list[Any]", argv)):
        return None

    return {
        "command_id": "llm-suggested",
        "argv": [str(token) for token in cast("list[Any]", argv)],
        "working_directory": str(Path(snapshot.canonical_path_ref)),
        "kind": kind,
        "source": "llm_suggested",
    }


def _halt_for_command_approval(
    state: OptimizationState, ports: NodePorts, proposal: dict[str, Any]
) -> NodeExecution:
    case_id = _required_state_str(state, "case_id")
    content = canonical_json(proposal)
    proposal_ref = ports.artifacts.put_blob(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=sha256_digest(content),
        media_type="application/json",
    )
    now = datetime.now(UTC)
    interrupt = InterruptEnvelope(
        interrupt_id=f"{case_id}-A2-COMMAND-APPROVAL",
        case_id=case_id,
        thread_id=_required_state_str(state, "thread_id"),
        stage="A2.31",
        artifact_digest=proposal_ref.content_digest,
        allowed_decisions=["approve", "reject"],
        required_actor_role="owner",
        policy_version="a2-command-approval-v1",
        issued_at=now,
        expires_at=now + timedelta(hours=24),
    )
    return NodeExecution(
        route=NodeRoute.APPROVAL,
        updates={
            "artifact_refs": [proposal_ref],
            "pending_interrupt": interrupt,
            "pending_command_proposal": proposal,
        },
    )


_WORKER_POLL_INTERVAL_SECONDS = 0.05
_WORKER_POLL_MAX_SECONDS = 600.0


def _worker_job_id(case_id: str, command_id: str) -> str:
    return f"{case_id}:A2.50:{command_id}"


def _await_worker_result(
    ports: NodePorts, *, job_id: str, timeout_seconds: int
) -> ArtifactRef | None:
    """Bounded poll for a job `_a2_50` already submitted through `ports.workers`.

    Capped at `_WORKER_POLL_MAX_SECONDS` regardless of the job's own budget so
    one slow local command cannot stall a handler indefinitely; `None` means
    "not observed complete within the wait window", which the caller records
    as `unavailable_reason` rather than treating as failure.
    """

    if ports.workers is None:
        return None
    deadline = time.monotonic() + min(float(timeout_seconds), _WORKER_POLL_MAX_SECONDS)
    while True:
        result = ports.workers.reconcile(job_id=job_id, idempotency_key=job_id)
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            return None
        time.sleep(_WORKER_POLL_INTERVAL_SECONDS)


def _evidence_identity(
    request: OptimizationRequest,
    snapshot: SourceSnapshot,
    env: EnvironmentManifest,
    *,
    sample_id: str,
    collector: str,
    collector_version: str,
    action_id: str | None = None,
    binding: CollectorBinding | None = None,
) -> EvidenceIdentity:
    return EvidenceIdentity(
        feature_id=request.objective.feature_id,
        repository_id=snapshot.repository_id,
        source_snapshot_digest=snapshot.content_digest,
        workload_id=request.workload.workload_id,
        dataset_id=request.workload.dataset_id,
        environment_id=request.workload.environment_id,
        hardware_profile=env.hardware_profile,
        concurrency=env.concurrency,
        cache_state=env.cache_state,
        metric_schema_version="1.0",
        sample_id=sample_id,
        observed_at=datetime.now(UTC),
        collector=collector,
        collector_version=collector_version,
        action_id=action_id,
        recipe_id=binding.recipe_id if binding else None,
        recipe_version=binding.recipe_version if binding else None,
    )


def _collect_registered_recipe_evidence(
    state: OptimizationState,
    ports: NodePorts,
    *,
    node_id: str,
    request: OptimizationRequest,
    snapshot: SourceSnapshot,
    environment: EnvironmentManifest,
    authorization: ExecutionAuthorization,
    plan: CollectorPlan,
) -> tuple[list[EvidenceItem], dict[str, float], list[str]]:
    evidence: list[EvidenceItem] = []
    coverage: dict[str, float] = {}
    unavailable: list[str] = []
    tenant_id = _required_state_str(state, "tenant_id")
    case_id = _required_state_str(state, "case_id")
    bindings = [
        binding
        for binding in plan.bindings
        if binding.execution_node == node_id and binding.executor_capability is not None
    ]
    for binding in bindings:
        action_id = f"recipe:{binding.requirement_id}"
        job_id = _worker_job_id(case_id, action_id)
        if action_id not in authorization.authorized_action_ids:
            unavailable.append(f"{binding.requirement_id}: recipe action was not authorized")
            coverage[binding.requirement_id] = 0.0
            continue
        raw_ref = _await_worker_result(
            ports, job_id=job_id, timeout_seconds=request.budget.maximum_worker_seconds
        )
        if raw_ref is None:
            unavailable.append(f"{binding.requirement_id}: recipe action did not complete")
            coverage[binding.requirement_id] = 0.0
            continue
        try:
            observations = _decode_recipe_output(
                ports.artifacts.read(tenant_id=tenant_id, ref=raw_ref), binding, ports
            )
        except (ValueError, TypeError) as exc:
            unavailable.append(f"{binding.requirement_id}: decoder failed: {exc}")
            coverage[binding.requirement_id] = 0.0
            continue
        requirement = _requirement_for_binding(request, binding)
        for index, (value, unit, extra_dimensions) in enumerate(observations, start=1):
            identity = _evidence_identity(
                request,
                snapshot,
                environment,
                sample_id=f"{binding.requirement_id}-{index}",
                collector=binding.collector_id,
                collector_version=binding.recipe_version,
                action_id=action_id,
                binding=binding,
            )
            evidence.append(
                EvidenceItem(
                    evidence_id=f"{case_id}:{node_id}:{binding.requirement_id}:{index}",
                    evidence_type=binding.source_type,
                    trust_level=TrustLevel.T2,
                    identity=identity,
                    raw_ref=raw_ref,
                    value=value,
                    unit=unit,
                    requirement_id=binding.requirement_id,
                    criterion_id=requirement.criterion_id if requirement else None,
                    metric_id=binding.metric_id,
                    value_schema=binding.output_schema,
                    transformation_id=binding.decoder_id,
                    dimensions={
                        **_evidence_dimensions(request, environment),
                        **extra_dimensions,
                    },
                )
            )
        coverage[binding.requirement_id] = float(len(observations))
    return evidence, coverage, unavailable


type _JsonValue = str | int | float | bool | list["_JsonValue"] | dict[str, "_JsonValue"] | None


def _decode_recipe_output(
    raw_bytes: bytes, binding: CollectorBinding, ports: NodePorts
) -> list[tuple[float | str | bool | None, str | None, dict[str, str]]]:
    if binding.decoder_id != "json-selector/v1":
        if ports.evidence_decoders is None or not ports.evidence_decoders.healthcheck():
            raise ValueError(f"decoder {binding.decoder_id!r} is unavailable")
        decoded = ports.evidence_decoders.decode(
            decoder_id=binding.decoder_id,
            content=raw_bytes,
            output_schema=binding.output_schema,
            value_selector=binding.value_selector,
        )
        if not decoded:
            raise ValueError("decoder produced no observations")
        return [(item.value, item.unit, item.dimensions) for item in decoded]
    try:
        payload = cast(_JsonValue, json.loads(raw_bytes))
    except (TypeError, ValueError) as exc:
        raise ValueError("registered decoder requires JSON output") from exc
    selected = payload
    if binding.value_selector:
        selector = binding.value_selector.removeprefix("$.")
        for component in selector.split("."):
            if isinstance(selected, dict) and component in selected:
                selected = selected[component]
            else:
                raise ValueError(f"value selector {binding.value_selector!r} did not resolve")
    entries = selected if isinstance(selected, list) else [selected]
    observations: list[tuple[float | str | bool | None, str | None, dict[str, str]]] = []
    for entry in entries:
        if isinstance(entry, dict):
            value = entry.get("value")
            unit = entry.get("unit") or binding.canonical_unit
            dimensions_raw = entry.get("dimensions") or {}
            if not isinstance(dimensions_raw, dict):
                raise ValueError("observation dimensions must be an object")
            dimensions = {str(key): str(value) for key, value in dimensions_raw.items()}
        else:
            value = entry
            unit = binding.canonical_unit
            dimensions = {}
        if value is not None and not isinstance(value, int | float | str | bool):
            raise ValueError("decoded observation value is not scalar")
        observations.append((value, str(unit) if unit is not None else None, dimensions))
    if not observations:
        raise ValueError("decoder produced no observations")
    return observations


def _run_command_evidence_branch(
    state: OptimizationState,
    ports: NodePorts,
    *,
    node_id: str,
    branch_kind: Literal["static", "test", "metric"],
    command_kinds: tuple[str, ...],
) -> NodeExecution:
    """Shared A2.60/A2.61/A2.62 implementation: wrap a real command's worker output.

    Handles all three uniformly because "no eligible command of this kind"
    is a first-class, honest outcome (`unavailable_reason`), not a distinct
    code path — this is exactly how A2.62 (benchmark) stays real without a
    benchmark adapter: A2.31 never emits a `benchmark` command today, so this
    always reports unavailable for it instead of fabricating a sample.
    """

    if ports.workers is None:
        raise RuntimeError(f"{node_id} requires WorkerBroker wired into NodePorts")

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    verification = _read_model(
        ports, state, _require_ref(state, "VerificationManifest"), VerificationManifest
    )
    env = _read_model(ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest)
    authorization = _read_model(
        ports, state, _require_ref(state, "ExecutionAuthorization"), ExecutionAuthorization
    )
    plan = _read_model(ports, state, _require_ref(state, "CollectorPlan"), CollectorPlan)
    case_id = _required_state_str(state, "case_id")
    tenant_id = _required_state_str(state, "tenant_id")

    evidence: list[EvidenceItem] = []
    coverage: dict[str, float] = {}
    unavailable: list[str] = []

    for kind in command_kinds:
        command = next((c for c in verification.commands if c.kind == kind), None)
        if command is None:
            unavailable.append(f"{kind}: no repository-owned command detected by A2.31")
            coverage[kind] = 0.0
            continue

        job_id = _worker_job_id(case_id, command.command_id)
        if job_id not in authorization.worker_job_ids:
            unavailable.append(f"{kind}: not authorized by A2.50")
            coverage[kind] = 0.0
            continue

        raw_ref = _await_worker_result(
            ports, job_id=job_id, timeout_seconds=request.budget.maximum_worker_seconds
        )
        if raw_ref is None:
            unavailable.append(f"{kind}: worker job {job_id} did not complete in time")
            coverage[kind] = 0.0
            continue

        payload = TypeAdapter(dict[str, Any]).validate_json(
            ports.artifacts.read(tenant_id=tenant_id, ref=raw_ref)
        )

        if kind == "benchmark":
            rounds = cast("list[dict[str, Any]]", payload.get("benchmark_rounds") or [])
            if not rounds:
                unavailable.append(f"{kind}: benchmark ran but produced no round data")
                coverage[kind] = 0.0
                continue
            for round_index, round_data in enumerate(rounds, start=1):
                targets = _bindings_for_observation(plan, node_id=node_id, source_type="benchmark")
                for target_index, binding in enumerate(targets, start=1):
                    identity = _evidence_identity(
                        request,
                        snapshot,
                        env,
                        sample_id=f"{command.command_id}-{round_index}-{target_index}",
                        collector="a2-local-worker-broker",
                        collector_version="1.0.0",
                        action_id=command.command_id,
                        binding=binding,
                    )
                    requirement = _requirement_for_binding(request, binding)
                    evidence.append(
                        EvidenceItem(
                            evidence_id=(
                                f"{case_id}:{node_id}:{command.command_id}-"
                                f"{round_index}-{target_index}"
                            ),
                            evidence_type=f"{kind}_command_result",
                            trust_level=TrustLevel.T2,
                            identity=identity,
                            raw_ref=raw_ref,
                            value=float(cast("float", round_data["value"])),
                            unit=cast("str", round_data.get("unit", "seconds")),
                            requirement_id=binding.requirement_id if binding else None,
                            criterion_id=requirement.criterion_id if requirement else None,
                            metric_id=binding.metric_id if binding else "benchmark_duration",
                            value_schema=binding.output_schema if binding else "scalar/v1",
                            transformation_id="raw/v1",
                            dimensions=_evidence_dimensions(request, env),
                        )
                    )
            coverage[kind] = 1.0
            continue

        exit_code = payload.get("exit_code")

        source_type = "test" if kind == "unit" else "static"
        targets = _bindings_for_observation(plan, node_id=node_id, source_type=source_type)
        for target_index, binding in enumerate(targets, start=1):
            identity = _evidence_identity(
                request,
                snapshot,
                env,
                sample_id=f"{command.command_id}-{target_index}",
                collector="a2-local-worker-broker",
                collector_version="1.0.0",
                action_id=command.command_id,
                binding=binding,
            )
            requirement = _requirement_for_binding(request, binding)
            evidence.append(
                EvidenceItem(
                    evidence_id=f"{case_id}:{node_id}:{command.command_id}-{target_index}",
                    evidence_type=f"{kind}_command_result",
                    trust_level=TrustLevel.T2,
                    identity=identity,
                    raw_ref=raw_ref,
                    value=float(exit_code) if isinstance(exit_code, int | float) else None,
                    unit="exit_code",
                    requirement_id=binding.requirement_id if binding else None,
                    criterion_id=requirement.criterion_id if requirement else None,
                    metric_id=(binding.metric_id if binding else f"{kind}_command_result"),
                    value_schema=binding.output_schema if binding else "scalar/v1",
                    transformation_id="raw/v1",
                    dimensions=_evidence_dimensions(request, env),
                )
            )
        coverage[kind] = 1.0

    recipe_evidence, recipe_coverage, recipe_unavailable = _collect_registered_recipe_evidence(
        state,
        ports,
        node_id=node_id,
        request=request,
        snapshot=snapshot,
        environment=env,
        authorization=authorization,
        plan=plan,
    )
    evidence.extend(recipe_evidence)
    coverage.update(recipe_coverage)
    unavailable.extend(recipe_unavailable)

    branch = _seal(
        BranchEvidenceRefs(
            **_branch_envelope(
                state,
                node_id,
                parents=[
                    snapshot.content_digest,
                    verification.content_digest,
                    authorization.content_digest,
                    plan.content_digest,
                ],
            ),
            branch_id=node_id,
            branch_kind=branch_kind,
            evidence=evidence,
            coverage=coverage,
            unavailable_reason="; ".join(unavailable) if unavailable and not evidence else None,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id=node_id)
    return NodeExecution(updates={"artifact_refs": [ref]})


def _run_compose_evidence_branch(
    state: OptimizationState, ports: NodePorts, *, request: OptimizationRequest
) -> NodeExecution:
    """Convert validated evaluator JSON into requirement-bound A2 evidence."""

    if ports.workers is None:
        raise RuntimeError("A2.62 requires WorkerBroker wired into NodePorts")
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    environment = _read_model(
        ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest
    )
    authorization = _read_model(
        ports, state, _require_ref(state, "ExecutionAuthorization"), ExecutionAuthorization
    )
    action_id = "compose:evaluation"
    job_id = _worker_job_id(_required_state_str(state, "case_id"), action_id)
    evidence: list[EvidenceItem] = []
    coverage: dict[str, float] = {}
    unavailable: list[str] = []
    raw_ref: ArtifactRef | None = None
    if action_id not in authorization.authorized_action_ids:
        unavailable.append("compose evaluation was not authorized by A2.50")
    else:
        raw_ref = _await_worker_result(
            ports, job_id=job_id, timeout_seconds=request.budget.maximum_worker_seconds
        )
        if raw_ref is None:
            unavailable.append("compose evaluation did not complete in time")
        else:
            result = ComposeEvaluationWorkerResult.model_validate_json(
                ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=raw_ref)
            )
            if result.lifecycle_error:
                unavailable.append(result.lifecycle_error)
            if not result.cleanup_confirmed:
                unavailable.append("Compose cleanup could not be confirmed")
            for attempt in result.attempts:
                if attempt.output is None:
                    unavailable.append(
                        f"{attempt.evaluation_id} repetition {attempt.repetition}: "
                        f"{attempt.error or 'invalid evaluator output'}"
                    )
                    continue
                for requirement in request.evidence_requirements:
                    if requirement.metric_id not in attempt.output.metrics:
                        continue
                    value = _canonical_metric_value(
                        attempt.output.metrics[requirement.metric_id],
                        canonical_unit=requirement.canonical_unit,
                    )
                    source_type = sorted(requirement.accepted_source_types)[0]
                    identity = _evidence_identity(
                        request,
                        snapshot,
                        environment,
                        sample_id=(
                            f"{attempt.evaluation_id}-{attempt.repetition}-"
                            f"{requirement.requirement_id}"
                        ),
                        collector="compose-evaluator",
                        collector_version="1.0.0",
                        action_id=action_id,
                    )
                    evidence.append(
                        EvidenceItem(
                            evidence_id=(
                                f"{_required_state_str(state, 'case_id')}:A2.62:"
                                f"{attempt.evaluation_id}:{attempt.repetition}:"
                                f"{requirement.requirement_id}"
                            ),
                            evidence_type=source_type,
                            trust_level=TrustLevel.T2,
                            identity=identity,
                            raw_ref=raw_ref,
                            value=value,
                            unit=requirement.canonical_unit,
                            requirement_id=requirement.requirement_id,
                            criterion_id=requirement.criterion_id,
                            metric_id=requirement.metric_id,
                            value_schema="scalar/v1",
                            transformation_id="compose-evaluator-json/v1",
                            dimensions=_evidence_dimensions(request, environment),
                        )
                    )
                    coverage[requirement.requirement_id] = (
                        coverage.get(requirement.requirement_id, 0.0) + 1.0
                    )
    branch = _seal(
        BranchEvidenceRefs(
            **_branch_envelope(
                state,
                "A2.62",
                parents=[snapshot.content_digest, authorization.content_digest],
            ),
            branch_id="A2.62",
            branch_kind="metric",
            evidence=evidence,
            coverage=coverage,
            unavailable_reason="; ".join(unavailable) if unavailable else None,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A2.62")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _canonical_metric_value(
    value: float | int | bool | None, *, canonical_unit: str | None
) -> float | int | None:
    """Translate evaluator verdicts into the requirement's canonical unit.

    Python treats ``bool`` as an ``int`` (``True == 1``), but an exit-code
    guardrail uses the opposite success convention: zero means pass. Perform
    the conversion at the evaluator boundary so aggregation and every
    downstream lane see one unambiguous representation.
    """

    if canonical_unit == "exit_code" and isinstance(value, bool):
        return 0 if value else 1
    return value


_FAN_IN_BRANCH_IDS = ("A2.60", "A2.61", "A2.62", "A2.63", "A2.64")


# Maps `EvidenceItem.evidence_type` (as emitted by A2.60/A2.61/A2.64) to the
# `source_type` vocabulary `EvidenceRequirement.accepted_source_types` uses
# (a1.py). A2.62/A2.63 never populate evidence today (see registry.A2_BLOCKED_NODES
# comment above `_run_command_evidence_branch`), so "benchmark"/"telemetry"
# never actually appear on the right of this map yet — it is here so A2.90's
# coverage check is correct the moment those branches start producing.
_EVIDENCE_TYPE_SOURCE_TYPE: dict[str, str] = {
    "unit_command_result": "test",
    "lint_command_result": "static",
    "type_command_result": "static",
    "benchmark_command_result": "benchmark",
    "source_map": "source_map",
}

_UNIT_FACTORS: dict[tuple[str, str], float] = {
    ("seconds", "ms"): 1000.0,
    ("s", "ms"): 1000.0,
    ("milliseconds", "ms"): 1.0,
    ("bytes", "kb"): 1.0 / 1000.0,
    ("bytes", "mb"): 1.0 / 1_000_000.0,
    ("fraction", "percent"): 100.0,
}

# Source types whose repeated observations are genuine samples from a
# distribution. `pytest-benchmark` really does emit one `EvidenceItem` per
# calibrated round (see `_a2_6x`'s benchmark branch), so a mean over them
# means something; telemetry behaves the same way once a collector exists.
_SAMPLED_SOURCE_TYPES = frozenset({"benchmark", "telemetry"})


def _bindings_for_observation(
    plan: CollectorPlan, *, node_id: str, source_type: str
) -> list[CollectorBinding | None]:
    bindings = [
        binding
        for binding in plan.bindings
        if binding.execution_node == node_id
        and binding.source_type == source_type
        and binding.executor_capability is None
    ]
    return cast("list[CollectorBinding | None]", bindings) or [None]


def _requirement_for_binding(
    request: OptimizationRequest, binding: CollectorBinding | None
) -> EvidenceRequirement | None:
    if binding is None:
        return None
    return next(
        (
            requirement
            for requirement in request.evidence_requirements
            if requirement.requirement_id == binding.requirement_id
        ),
        None,
    )


def _evidence_dimensions(
    request: OptimizationRequest, environment: EnvironmentManifest
) -> dict[str, str]:
    return {
        "workload_id": request.workload.workload_id,
        "environment_id": request.workload.environment_id,
        "cache_state": environment.cache_state,
        "hardware_profile": environment.hardware_profile,
        "concurrency": str(environment.concurrency),
        **environment.dimensions,
    }


def _effective_minimum_samples(
    requirement: EvidenceRequirement, matching: list[EvidenceItem]
) -> int:
    """`minimum_samples`, except deterministic evidence is complete at one.

    `minimum_samples` encodes measurement rigour: a latency criterion needs
    several timed samples before its mean is meaningful. A command exit code
    is not a sample from a distribution -- it is one deterministic verdict,
    and re-running the same suite against the same immutable snapshot yields
    an identical second copy, not more evidence. Requiring three of them made
    every correctness-only case permanently unsatisfiable at this gate (A1.71
    asks for `workload.repetitions` samples; the command collectors emit
    exactly one) while adding no information, so a requirement served purely
    by deterministic evidence is satisfied by a single observation.

    Requirements that any sampled collector contributed to keep the full
    count, and a requirement with no matching evidence at all still fails --
    this narrows what "enough evidence" means, it does not remove the gate.
    """

    if requirement.aggregation == "verdict":
        return 1
    if not matching:
        return requirement.minimum_samples
    if any(
        _EVIDENCE_TYPE_SOURCE_TYPE.get(item.evidence_type) in _SAMPLED_SOURCE_TYPES
        for item in matching
    ):
        return requirement.minimum_samples
    return 1


def _freshness_limit(request: OptimizationRequest, item: EvidenceItem) -> int:
    requirement = next(
        (
            candidate
            for candidate in request.evidence_requirements
            if candidate.requirement_id == item.requirement_id
        ),
        None,
    )
    if requirement is not None and requirement.freshness_seconds is not None:
        return requirement.freshness_seconds
    return request.budget.deadline_seconds


def _normalize_observation(
    item: EvidenceItem, binding: CollectorBinding | None
) -> tuple[float | str | bool | None, str | None, str]:
    if binding is None or binding.canonical_unit is None or item.unit == binding.canonical_unit:
        return item.value, item.unit, f"{item.transformation_id}:identity"
    if not isinstance(item.value, int | float) or item.unit is None:
        raise ValueError("non-numeric observation cannot be converted to the canonical unit")
    factor = _UNIT_FACTORS.get((item.unit.lower(), binding.canonical_unit.lower()))
    if factor is None:
        raise ValueError(
            f"no registered conversion from {item.unit!r} to {binding.canonical_unit!r}"
        )
    return (
        float(item.value) * factor,
        binding.canonical_unit,
        f"{item.transformation_id}:unit-conversion-v1",
    )


_REDACTION_KEYWORDS = ("password", "secret", "api_key", "apikey", "token=")
_TRUST_RANK = {
    TrustLevel.T0: 0,
    TrustLevel.T1: 1,
    TrustLevel.T2: 2,
    TrustLevel.T3: 3,
    TrustLevel.T4: 4,
}


_BUILTIN_EXECUTION_NODES: dict[str, Literal["A2.60", "A2.61", "A2.62", "A2.63", "A2.64"]] = {
    "static": "A2.60",
    "test": "A2.61",
    "benchmark": "A2.62",
    "telemetry": "A2.63",
    "source_map": "A2.64",
}


def _resolve_binding(
    requirement: EvidenceRequirement,
    *,
    ports: NodePorts,
    state: OptimizationState,
) -> tuple[CollectorBinding, int | None] | None:
    """Resolve a semantic requirement to a versioned evidence recipe.

    Registry records may introduce any executor capability and output schema;
    no A2 core change is required. Built-ins exist only as compatibility
    recipes for the local command/source-map adapters shipped in this repo.
    """

    metric_id = requirement.metric_id or requirement.criterion_id
    if ports.registry is not None:
        records = ports.registry.list_active(
            tenant_id=_required_state_str(state, "tenant_id"),
            registry_kind=RegistryKind.COLLECTOR,
            at=datetime.now(UTC),
        )
        for record in sorted(records, key=lambda item: (item.record_id, -item.version)):
            try:
                recipe = EvidenceRecipeRegistration.model_validate(
                    {
                        "collector_id": record.record_id,
                        "recipe_id": record.record_id,
                        "recipe_version": str(record.version),
                        "executor_capability": record.record_id,
                        **record.payload,
                    }
                )
            except ValidationError:
                continue
            if metric_id not in recipe.supported_metric_ids:
                continue
            if (
                requirement.accepted_source_types
                and recipe.source_type not in requirement.accepted_source_types
            ):
                continue
            return (
                CollectorBinding(
                    requirement_id=requirement.requirement_id,
                    collector_id=recipe.collector_id,
                    source_type=recipe.source_type,
                    window_seconds=recipe.window_seconds,
                    aggregation=requirement.aggregation,
                    minimum_samples=requirement.minimum_samples,
                    metric_id=metric_id,
                    canonical_unit=requirement.canonical_unit,
                    recipe_id=recipe.recipe_id,
                    recipe_version=recipe.recipe_version,
                    execution_node=recipe.execution_node,
                    executor_capability=recipe.executor_capability,
                    decoder_id=recipe.decoder_id,
                    output_schema=recipe.output_schema,
                    value_selector=recipe.value_selector,
                    action_parameters=recipe.action_parameters,
                    required_dimensions=requirement.required_dimensions,
                    mandatory=requirement.mandatory,
                ),
                record.version,
            )

    for source_type in sorted(requirement.accepted_source_types):
        collector_id = _COLLECTOR_CATALOG.get(source_type)
        execution_node = _BUILTIN_EXECUTION_NODES.get(source_type)
        if collector_id is None or execution_node is None:
            continue
        return (
            CollectorBinding(
                requirement_id=requirement.requirement_id,
                collector_id=collector_id,
                source_type=source_type,
                window_seconds=3600,
                aggregation=requirement.aggregation,
                minimum_samples=requirement.minimum_samples,
                metric_id=metric_id,
                canonical_unit=requirement.canonical_unit,
                recipe_id=f"builtin:{source_type}",
                execution_node=execution_node,
                decoder_id=(
                    "benchmark-rounds/v1" if source_type == "benchmark" else "command-result/v1"
                ),
                required_dimensions=requirement.required_dimensions,
                mandatory=requirement.mandatory,
            ),
            None,
        )
    return None


def _read_pyproject(root: Path) -> dict[str, Any]:
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.exists():
        return {}
    try:
        with pyproject_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return _flatten_toml_sections(data)


_VENV_INTERPRETER_RELATIVE_PATHS = (
    Path(".venv") / "Scripts" / "python.exe",
    Path(".venv") / "bin" / "python",
    Path("venv") / "Scripts" / "python.exe",
    Path("venv") / "bin" / "python",
)


def _repository_interpreter(root: Path) -> str:
    """The Python that should run this repository's own commands.

    A repository that ships its own `.venv` is declaring where its
    dependencies live, so that interpreter wins -- running its test suite
    under an unrelated Python is how a real target ends up reporting import
    errors that say nothing about the code under analysis.

    Failing that, the ambient `python` is used, because a repository with no
    private environment was almost certainly installed into the operator's
    own. It is resolved to an absolute path *here* rather than left as the
    bare string `"python"`: `RepositoryCommand.argv` is sealed into the
    evidence chain, and "python" would record which binary ran as a
    late-bound PATH lookup nobody can audit afterwards.

    `sys.executable` is the last resort only: it is this control plane's
    environment, which deliberately contains the control plane's
    dependencies and not the target's.
    """

    for relative in _VENV_INTERPRETER_RELATIVE_PATHS:
        candidate = root / relative
        if candidate.is_file():
            return str(candidate)
    resolved = shutil.which("python") or shutil.which("python3")
    return resolved or sys.executable or "python"


_INI_TOOL_SECTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    # (config file, section name) pairs that mean the same thing as the
    # pyproject.toml key on the left. Only pyproject.toml was read before,
    # so every repository still using setup.cfg/tox.ini -- a large share of
    # real Python projects -- looked like it had no lint/type/test tooling
    # at all.
    "tool.pytest.ini_options": (("setup.cfg", "tool:pytest"), ("tox.ini", "pytest")),
    "tool.ruff": (("setup.cfg", "ruff"), ("ruff.toml", ""), (".ruff.toml", "")),
    "tool.mypy": (("setup.cfg", "mypy"), ("mypy.ini", "mypy"), (".mypy.ini", "mypy")),
}


def _declared_tools_outside_pyproject(root: Path) -> set[str]:
    """Which `tool.*` keys a repo declares in non-pyproject config files."""

    declared: set[str] = set()
    for tool_key, locations in _INI_TOOL_SECTIONS.items():
        for filename, section in locations:
            path = root / filename
            if not path.is_file():
                continue
            if not section:
                declared.add(tool_key)
                break
            parser = ConfigParser()
            try:
                parser.read(path, encoding="utf-8")
            except (OSError, UnicodeDecodeError, ConfigParserError):
                continue
            if parser.has_section(section):
                declared.add(tool_key)
                break
    return declared


def _requirements_declare_dependency(root: Path, package_name: str) -> bool:
    """Is `package_name` pinned in a `requirements*.txt` file?

    Same declarative signal `_pyproject_declares_dependency` reads out of
    pyproject.toml, just in the other place real projects put it -- and the
    same exact-token split, so e.g. a `pytest-covfefe==1.0` line can never
    false-positive-match `package_name="pytest-cov"` the way a bare
    `.startswith()` would.
    """

    for path in sorted(root.glob("requirements*.txt")):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            entry = line.split("#", 1)[0].strip().lower()
            if not entry:
                continue
            name = re.split(r"[><=!~\[; ]", entry, maxsplit=1)[0].replace("_", "-")
            if name == package_name:
                return True
    return False


def _requirements_declare_pytest_benchmark(root: Path) -> bool:
    return _requirements_declare_dependency(root, "pytest-benchmark")


def _pyproject_declares_dependency(pyproject_config: dict[str, Any], package_name: str) -> bool:
    """Does this repo declare `package_name` anywhere real dependencies live?

    Checks PEP 621 `[project.dependencies]`/`[project.optional-dependencies]`
    and PEP 735 `[dependency-groups]` (the form this very project's own
    pyproject.toml uses) -- the same declarative signal `tool_coverage`
    already uses for pytest/ruff/mypy, not an attempt to verify the package
    is actually importable in whatever environment ends up running the
    command (A2.60/61/62's shared runner surfaces that failure honestly if
    it isn't).
    """

    dependency_lists: list[Any] = []
    project = pyproject_config.get("project")
    if isinstance(project, dict):
        project_dict = cast("dict[str, Any]", project)
        dependency_lists.append(project_dict.get("dependencies", []))
        optional = project_dict.get("optional-dependencies")
        if isinstance(optional, dict):
            dependency_lists.extend(cast("dict[str, Any]", optional).values())
    groups = pyproject_config.get("dependency-groups")
    if isinstance(groups, dict):
        dependency_lists.extend(cast("dict[str, Any]", groups).values())

    for deps in dependency_lists:
        if not isinstance(deps, list):
            continue
        for dep in cast("list[Any]", deps):
            if not isinstance(dep, str):
                continue
            name = re.split(r"[><=!~\[; ]", dep.strip(), maxsplit=1)[0]
            if name.lower() == package_name:
                return True
    return False


def _has_pytest_benchmark_dependency(pyproject_config: dict[str, Any]) -> bool:
    return _pyproject_declares_dependency(pyproject_config, "pytest-benchmark")


def _has_pytest_cov_dependency(pyproject_config: dict[str, Any]) -> bool:
    return _pyproject_declares_dependency(pyproject_config, "pytest-cov")


def _requirements_declare_pytest_cov(root: Path) -> bool:
    return _requirements_declare_dependency(root, "pytest-cov")


def _flatten_toml_sections(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        flattened[path] = value
        if isinstance(value, dict):
            nested = cast("dict[str, Any]", value)
            flattened.update(_flatten_toml_sections(nested, path))
    return flattened


def _put_envelope(
    ports: NodePorts, state: OptimizationState, envelope: ArtifactEnvelope, *, node_id: str
) -> ArtifactRef:
    content = canonical_json(envelope.model_dump(mode="json", exclude={"content_digest"}))
    generic_ref = ports.artifacts.put_json(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=envelope.content_digest,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:{node_id}:{envelope.artifact_type}"
        ),
    )
    return ArtifactRef(
        artifact_type=envelope.artifact_type,
        schema_version=envelope.schema_version,
        artifact_id=envelope.artifact_id,
        content_digest=envelope.content_digest,
        uri=generic_ref.uri,
    )


def _read_model[T: BaseModel](
    ports: NodePorts,
    state: OptimizationState,
    ref: ArtifactRef,
    model: type[T],
) -> T:
    content = ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=ref)
    raw = TypeAdapter(dict[str, Any]).validate_json(content)
    if issubclass(model, ArtifactEnvelope):
        raw.setdefault("content_digest", ref.content_digest)
    return model.model_validate(raw)


def _seal[T: ArtifactEnvelope](model: T) -> T:
    return model.model_copy(update={"content_digest": model_content_digest(model)})


def _base_envelope(
    state: OptimizationState, artifact_type: str, *, parents: list[str] | None = None
) -> dict[str, Any]:
    return {
        "artifact_id": f"{_required_state_str(state, 'case_id')}-{artifact_type}",
        "tenant_id": _required_state_str(state, "tenant_id"),
        "case_id": _required_state_str(state, "case_id"),
        "created_at": datetime.now(UTC),
        "producer": _PRODUCER,
        "policy_versions": {"a2": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    raise ValueError(f"missing required artifact ref: {artifact_type}")


def _branch_artifact_id(case_id: str, node_id: str) -> str:
    return f"{case_id}-{node_id}-BranchEvidenceRefs"


def _branch_envelope(
    state: OptimizationState, node_id: str, *, parents: list[str]
) -> dict[str, Any]:
    """`_base_envelope` for A2.60-A2.64, with a per-branch `artifact_id`.

    All five fan-out branches share `artifact_type="BranchEvidenceRefs"`. The
    default `_base_envelope` artifact_id (`f"{case_id}-{artifact_type}"`)
    would collide across all five, and `merge_artifact_refs`
    (`contracts/state.py`) keys on `(artifact_type, artifact_id)` — a
    collision there raises when LangGraph merges the parallel fan-out state
    updates, since each branch's content digest legitimately differs.
    """

    envelope = _base_envelope(state, "BranchEvidenceRefs", parents=parents)
    envelope["artifact_id"] = _branch_artifact_id(_required_state_str(state, "case_id"), node_id)
    return envelope


def _require_branch_ref(state: OptimizationState, node_id: str) -> ArtifactRef:
    artifact_id = _branch_artifact_id(_required_state_str(state, "case_id"), node_id)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == "BranchEvidenceRefs" and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required BranchEvidenceRefs for {node_id}")


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"A2 state is missing required field {key!r}")
    return value


def _materialize_source(
    request: OptimizationRequest, ports: NodePorts, state: OptimizationState
) -> tuple[Path, str, str | None]:
    if request.source.source_kind == "local_directory":
        allowed_root = Path(request.source.allowed_root_id).resolve()
        source_path = (allowed_root / request.source.relative_path).resolve()
        if not source_path.is_dir() or not source_path.is_relative_to(allowed_root):
            raise ValueError("local source path is unreachable or escapes its allowed root")
        return source_path, request.source.locator or str(source_path), None

    if ports.sources is None or request.source.locator is None:
        raise RuntimeError(
            f"source kind {request.source.source_kind!r} requires a configured SourceProvider"
        )
    materialization = ports.sources.acquire(
        SourceAcquisitionRequest(
            tenant_id=_required_state_str(state, "tenant_id"),
            repository_id=request.source.repository_id,
            source_kind=request.source.source_kind,
            locator=request.source.locator,
            requested_revision=request.source.requested_revision,
        )
    )
    source_path = Path(materialization.local_path).resolve()
    if not source_path.is_dir():
        raise ValueError("SourceProvider returned an unreadable materialization")
    return source_path, materialization.locator, materialization.resolved_revision


def _capture_stable_source(root: Path) -> tuple[list[tuple[str, bytes, bool]], list[str]]:
    """Capture a bounded source tree and reject concurrent mutations.

    Two attempts are allowed. Each attempt compares the complete eligible
    path set and per-file stat before/after reading, so the sealed archive is
    never a mixture of two source states.
    """

    last_reason = "source changed while it was being captured"
    for _attempt in range(2):
        candidates, exclusions = _source_candidates(root)
        captured: list[tuple[str, bytes, bool]] = []
        stable = True
        for relative, path in candidates:
            try:
                before = path.stat()
                content = path.read_bytes()
                after = path.stat()
            except OSError as exc:
                last_reason = f"cannot capture {relative!r}: {exc}"
                stable = False
                break
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                last_reason = f"source file changed during capture: {relative}"
                stable = False
                break
            captured.append((relative, content, bool(after.st_mode & 0o111)))
        if stable and [relative for relative, _ in _source_candidates(root)[0]] == [
            relative for relative, _, _ in captured
        ]:
            return captured, exclusions
    raise ValueError(last_reason)


def _source_candidates(root: Path) -> tuple[list[tuple[str, Path]], list[str]]:
    candidates: list[tuple[str, Path]] = []
    exclusions: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_disallowed_path(relative):
            exclusions.append(relative)
        else:
            candidates.append((relative, path))
    return candidates, exclusions


def _build_source_archive(captured: list[tuple[str, bytes, bool]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, content, executable in captured:
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def _materialize_immutable_workspace(
    captured: list[tuple[str, bytes, bool]], archive_digest: str
) -> Path:
    target = (
        Path(tempfile.gettempdir())
        / "production-optimizer"
        / "a2-snapshots"
        / archive_digest.removeprefix("sha256:")
    ).resolve()
    target.mkdir(parents=True, exist_ok=True)
    expected = {relative for relative, _, _ in captured}
    existing = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
    if existing - expected:
        raise ValueError("immutable snapshot workspace contains unexpected files")
    for relative, content, executable in captured:
        destination = (target / relative).resolve()
        if not destination.is_relative_to(target):
            raise ValueError(f"source archive path escapes snapshot workspace: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != content:
                raise ValueError("immutable snapshot workspace digest collision")
            continue
        destination.write_bytes(content)
        destination.chmod(0o555 if executable else 0o444)
    return target


def _git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _is_disallowed_path(relative: str) -> bool:
    parts = set(relative.split("/"))
    return bool(parts & _DISALLOWED_PATHS)
