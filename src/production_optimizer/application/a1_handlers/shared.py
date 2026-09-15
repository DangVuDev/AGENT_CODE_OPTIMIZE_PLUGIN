# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from production_optimizer.application.metrics import resolve_metric
from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a1 import (
    A1ApprovalDecision,
    A1QualityReport,
    ApprovalBinding,
    CanonicalObjective,
    Criterion,
    CriterionSet,
    EvidenceRequirement,
    EvidenceRequirementSet,
    ExecutionBudget,
    ExecutionBudgetArtifact,
    FeatureScope,
    Guardrail,
    GuardrailSet,
    IntakeEnvelope,
    LocalSourceIdentity,
    ManualCasePayload,
    Objective,
    OptimizationRequest,
    Origin,
    ProjectProfile,
    RawRequestDraft,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
    WorkloadIdentity,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.evaluation import ComposeExecutionContract
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import (
    ActorContext,
    ModelCompletionRequest,
    ModelMessage,
    ModelRole,
    PolicyRequest,
)
from production_optimizer.contracts.state import OptimizationState

_logger = logging.getLogger(__name__)

_PRODUCER = ProducerIdentity(name="a1-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_MAX_DISCOVERY_FILES = 500
_DEFAULT_MODEL_ID = "a1-intent-extractor"
_A1_EXTRACTION_PROMPT_VERSION = "a1-intent-extraction-v1"
_REQUIRED_DRAFT_FIELDS = (
    "feature_id",
    "metric_id",
    "direction",
    "target",
    "unit",
    "workload_id",
    "environment_id",
    "command_id",
)

# Language-specific workload defaults (Phase 3): minimize collection time on
# the first run while still capturing real variance where a runtime actually
# needs it (JVM/.NET/JS warmup, Go/Rust microbenchmarks). Looked up by
# `_most_common_language(ProjectProfile.languages)` in `_a1_70`.
_WORKLOAD_DEFAULTS_BY_LANGUAGE: dict[str, dict[str, int]] = {
    "python": {"repetitions": 1, "warmup_runs": 0},
    "go": {"repetitions": 2, "warmup_runs": 1},
    "rust": {"repetitions": 2, "warmup_runs": 1},
    "typescript": {"repetitions": 3, "warmup_runs": 1},
    "javascript": {"repetitions": 3, "warmup_runs": 1},
    "java": {"repetitions": 2, "warmup_runs": 1},
    "csharp": {"repetitions": 2, "warmup_runs": 1},
}
_WORKLOAD_DEFAULTS_FALLBACK: dict[str, int] = {"repetitions": 1, "warmup_runs": 0}


class _ExtractedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    objective_statement: str | None = Field(default=None, max_length=4000)
    feature_id: str | None = Field(default=None, max_length=255)
    metric_id: str | None = Field(default=None, max_length=255)
    direction: Literal["minimize", "maximize", "target"] | None = None
    target: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=100)
    guardrail_metric_id: str | None = Field(default=None, max_length=255)
    workload_id: str | None = Field(default=None, max_length=255)
    dataset_id: str | None = Field(default=None, max_length=255)
    environment_id: str | None = Field(default=None, max_length=255)
    command_id: str | None = Field(default=None, max_length=255)
    requester_hypothesis: str | None = Field(default=None, max_length=2000)
    confidence: float = Field(default=0.0, ge=0, le=1)


def build_a1_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build a runtime with production A1 handlers registered.

    This is intentionally scoped to the A1 subgraph. A2/A3/C0 production
    handlers are separate delivery waves because they introduce collectors,
    analyzers, workers and model-provider policies.
    """

    return NodeRuntime(build_a1_registrations(), ports=ports)


def build_a1_registrations() -> dict[str, RegisteredNode]:
    return build_bound_a1_registrations()


def _spec(node_id: str) -> NodeSpec:
    routes = {NodeRoute.CONTINUE.value}
    if node_id == "A1.80":
        routes |= {NodeRoute.CLARIFICATION.value, NodeRoute.REJECTED.value}
    if node_id == "A1.90":
        routes |= {NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value}
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-1-a1",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="a1-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=30,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/implementation/05-lane-1-detailed-implementation-playbook.md",
        slo="A1 node completes within 30 seconds for local repositories below policy limits",
    )


def _handler(node_id: str) -> Any:
    # Lazy import avoids a package-import cycle while keeping the registry
    # as the single ID-to-callable authority.
    from .registry import NODE_HANDLERS

    return NODE_HANDLERS[node_id]


def build_bound_a1_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import A1_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in A1_NODE_IDS
    }


def _most_common_language(languages: dict[str, float]) -> str:
    """Return the language with the highest discovered proportion.

    Falls back to `"unknown"` when `ProjectProfile.languages` is empty (no
    recognized source files, or discovery found nothing) so the caller's
    dict lookup always lands on `_WORKLOAD_DEFAULTS_FALLBACK`.
    """

    if not languages:
        return "unknown"
    return max(languages, key=lambda lang: languages[lang])


def _draft_from_payload(
    state: OptimizationState, ports: NodePorts, payload: ManualCasePayload
) -> RawRequestDraft:
    extracted = _extract_raw_intent(state, ports, payload)
    fields = (
        "feature_id",
        "metric_id",
        "direction",
        "target",
        "unit",
        "guardrail_metric_id",
        "workload_id",
        "dataset_id",
        "environment_id",
        "command_id",
        "requester_hypothesis",
    )
    values: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    conflicts: list[str] = []
    # A multi-criterion request states its criteria in `payload.criteria`, so
    # the four single-criterion shorthand fields arrive empty. Mirror the
    # highest-weighted one into them: A1.60's objective derivation, A1.80's
    # required-field check and `_REQUIRED_DRAFT_FIELDS` all read the
    # shorthand, and the primary criterion is exactly what they mean by it.
    # A1.61 still seals every criterion from `draft.criteria`.
    if payload.criteria:
        primary = max(payload.criteria, key=lambda criterion: criterion.weight)
        for field_name, value in (
            ("metric_id", primary.metric_id),
            ("direction", primary.direction),
            ("target", primary.target),
            ("unit", primary.unit),
        ):
            values[field_name] = value
            provenance[field_name] = "structured_payload:criteria"
    if payload.execution_profile == "docker_compose" and payload.evaluations:
        values["command_id"] = payload.evaluations[0].evaluation_id
        provenance["command_id"] = "structured_payload:evaluation"
    for field_name in fields:
        raw_value = getattr(extracted, field_name, None) if extracted is not None else None
        structured_value = getattr(payload, field_name)
        if raw_value is not None:
            values[field_name] = raw_value
            provenance[field_name] = "raw_text:model"
        if structured_value is not None:
            if raw_value is not None and raw_value != structured_value:
                conflicts.append(f"{field_name}: raw and structured values disagree")
            values[field_name] = structured_value
            provenance[field_name] = "structured_payload"

    if not values.get("guardrail_metric_id"):
        values["guardrail_metric_id"] = "unit_command_result"
        provenance["guardrail_metric_id"] = "policy_default"
    objective_statement = (
        extracted.objective_statement if extracted is not None else None
    ) or payload.raw_text
    if not objective_statement and values.get("feature_id") and values.get("metric_id"):
        objective_statement = (
            f"Optimize {values['feature_id']}: {values['metric_id']} "
            f"({values.get('direction') or 'unspecified'})"
        )
        provenance["objective_statement"] = "structured_payload:derived"
    elif objective_statement:
        provenance["objective_statement"] = "raw_text"

    unresolved = [field for field in _REQUIRED_DRAFT_FIELDS if values.get(field) is None]
    confidence = 1.0 if not payload.raw_text else (extracted.confidence if extracted else 0.0)
    if conflicts:
        confidence = min(confidence, 0.5)
    draft = RawRequestDraft(
        **_base_envelope(state, "RawRequestDraft"),
        origin=Origin.MANUAL,
        objective_statement=objective_statement,
        feature_id=values.get("feature_id"),
        metric_id=values.get("metric_id"),
        direction=values.get("direction"),
        target=values.get("target"),
        unit=values.get("unit"),
        criteria=list(payload.criteria),
        guardrail_metric_id=values.get("guardrail_metric_id"),
        workload_id=values.get("workload_id"),
        dataset_id=values.get("dataset_id"),
        environment_id=values.get("environment_id"),
        command_id=values.get("command_id"),
        extraction_confidence=confidence,
        unresolved_fields=unresolved,
        requester_hypothesis=values.get("requester_hypothesis"),
        field_provenance=provenance,
        conflicts=conflicts,
    )
    return _seal(draft)


def _extract_raw_intent(
    state: OptimizationState, ports: NodePorts, payload: ManualCasePayload
) -> _ExtractedIntent | None:
    if payload.raw_text is None or ports.model is None:
        return None
    schema = _ExtractedIntent.model_json_schema()
    base_messages = [
        ModelMessage(
            role="system",
            content=(
                "Extract only facts explicitly stated by the requester. Use null for unknowns; "
                "never invent paths, commands, metrics, units, targets, or workload identifiers. "
                "direction must be minimize, maximize, target, or null. Return JSON only."
            ),
        ),
        ModelMessage(role="user", content=payload.raw_text),
    ]
    token_budget = (
        payload.maximum_model_tokens if payload.maximum_model_tokens is not None else 1000
    )
    if token_budget <= 0:
        return None
    for attempt in range(2):
        messages = list(base_messages)
        if attempt:
            messages.append(
                ModelMessage(
                    role="user",
                    content=(
                        "The prior response was invalid. Return exactly one JSON object "
                        "matching the schema."
                    ),
                )
            )
        result = ports.model.complete(
            ModelCompletionRequest(
                role=ModelRole.GENERATOR,
                model_id=ports.model_id or _DEFAULT_MODEL_ID,
                prompt_version=_A1_EXTRACTION_PROMPT_VERSION,
                messages=messages,
                response_schema=schema,
                max_output_tokens=max(1, min(500, token_budget)),
                idempotency_key=(
                    f"{_required_state_str(state, 'case_id')}:A1.20:extract:{attempt + 1}"
                ),
            )
        )
        consumed_tokens = result.input_tokens + result.output_tokens
        if consumed_tokens > token_budget:
            return None
        if result.valid_json and result.parsed_json is not None:
            try:
                return _ExtractedIntent.model_validate(result.parsed_json)
            except ValidationError:
                pass
        token_budget -= consumed_tokens
        if token_budget <= 0:
            break
    return None


def _input_mode(payload: ManualCasePayload) -> Literal["raw", "structured", "mixed"]:
    has_structured = any(getattr(payload, field) is not None for field in _REQUIRED_DRAFT_FIELDS)
    if payload.raw_text and has_structured:
        return "mixed"
    return "raw" if payload.raw_text else "structured"


def _discover_project_profile(state: OptimizationState, root: Path) -> ProjectProfile:
    suffix_counts: dict[str, int] = {}
    manifests: list[str] = []
    source_files: list[str] = []
    test_roots: set[str] = set()
    vendor: list[str] = []
    file_count = 0
    byte_count = 0
    unreadable_paths: list[str] = []
    truncated = False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_vendor_path(relative):
            vendor.append(relative)
            continue
        file_count += 1
        if file_count > _MAX_DISCOVERY_FILES:
            truncated = True
            break
        try:
            byte_count += path.stat().st_size
        except OSError:
            unreadable_paths.append(relative)
        source_files.append(relative)
        suffix = path.suffix.lower()
        if suffix:
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        if path.name in {"pyproject.toml", "package.json", "go.mod", "Cargo.toml"}:
            manifests.append(relative)
        if path.parent.name in {"test", "tests", "__tests__"}:
            test_roots.add(path.parent.relative_to(root).as_posix())

    total = sum(suffix_counts.values()) or 1
    languages = {
        _language_for_suffix(suffix): count / total
        for suffix, count in suffix_counts.items()
        if _language_for_suffix(suffix) is not None
    }
    profile = ProjectProfile(
        **_base_envelope(state, "ProjectProfile"),
        languages=cast("dict[str, float]", languages),
        manifest_files=sorted(manifests),
        test_roots=sorted(test_roots),
        source_files=sorted(source_files),
        generated_or_vendor_paths=sorted(vendor)[:100],
        file_count=file_count,
        byte_count=byte_count,
        discovery_truncated=truncated,
        unreadable_paths=unreadable_paths[:100],
    )
    return _seal(profile)


def _optimization_request(state: OptimizationState, ports: NodePorts) -> OptimizationRequest:
    source = _read_model(
        ports,
        state,
        _require_ref(state, "LocalSourceIdentity"),
        LocalSourceIdentity,
    )
    objective = _read_model(
        ports, state, _require_ref(state, "CanonicalObjective"), CanonicalObjective
    )
    scope = _read_model(ports, state, _require_ref(state, "FeatureScope"), FeatureScope)
    criteria = _read_model(ports, state, _require_ref(state, "CriterionSet"), CriterionSet)
    guardrails = _read_model(ports, state, _require_ref(state, "GuardrailSet"), GuardrailSet)
    budget = _read_model(
        ports,
        state,
        _require_ref(state, "ExecutionBudgetArtifact"),
        ExecutionBudgetArtifact,
    )
    workload = _read_model(ports, state, _require_ref(state, "WorkloadIdentity"), WorkloadIdentity)
    evidence = _read_model(
        ports, state, _require_ref(state, "EvidenceRequirementSet"), EvidenceRequirementSet
    )
    approval = _read_model(
        ports, state, _require_ref(state, "A1ApprovalDecision"), A1ApprovalDecision
    )
    if objective.objective is None:
        raise ValueError("A1.95 cannot freeze without CanonicalObjective.objective")
    if workload.workload is None:
        raise ValueError("A1.95 cannot freeze without WorkloadIdentity.workload")
    if approval.approval is None or not approval.approved:
        raise ValueError("A1.95 cannot freeze without an approved A1ApprovalDecision")
    fingerprint = _request_fingerprint(
        state,
        source=source,
        scope=scope,
        objective=objective,
        criteria=criteria,
        guardrails=guardrails,
        workload=workload,
        evidence=evidence,
        budget=budget,
    )
    if approval.approval.artifact_digest != fingerprint:
        raise ValueError("A1 approval digest does not match the frozen request fingerprint")
    request = OptimizationRequest(
        **_base_envelope(
            state,
            "OptimizationRequest",
            parents=[
                source.content_digest,
                scope.content_digest,
                objective.content_digest,
                criteria.content_digest,
                guardrails.content_digest,
                budget.content_digest,
                workload.content_digest,
                evidence.content_digest,
                approval.content_digest,
            ],
        ),
        origin=Origin.MANUAL,
        scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id=source.repository_id,
            allowed_root_id=source.allowed_root_id,
            relative_path=source.relative_path,
            requested_revision=source.git_revision,
        ),
        feature_scope=scope,
        objective=objective.objective,
        criteria=criteria.criteria,
        guardrails=guardrails.guardrails,
        workload=workload.workload,
        execution=workload.execution,
        evidence_requirements=evidence.evidence_requirements,
        budget=budget.budget,
        approval=approval.approval,
        request_fingerprint=fingerprint,
    )
    return _seal(request)


def _request_fingerprint(
    state: OptimizationState,
    *,
    source: LocalSourceIdentity,
    scope: FeatureScope,
    objective: CanonicalObjective,
    criteria: CriterionSet,
    guardrails: GuardrailSet,
    workload: WorkloadIdentity,
    evidence: EvidenceRequirementSet,
    budget: ExecutionBudgetArtifact,
) -> str:
    return sha256_digest(
        canonical_json(
            {
                "case_id": _required_state_str(state, "case_id"),
                "source": source.content_digest,
                "scope": scope.content_digest,
                "objective": objective.content_digest,
                "criteria": criteria.content_digest,
                "guardrails": guardrails.content_digest,
                "workload": workload.content_digest,
                "evidence": evidence.content_digest,
                "budget": budget.content_digest,
            }
        )
    )


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
        "created_at": _now(),
        "producer": _PRODUCER,
        "policy_versions": {"a1": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    raise ValueError(f"missing required artifact ref: {artifact_type}")


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"A1 state is missing required field {key!r}")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def _git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _repository_id(path: Path) -> str:
    return sha256_digest(str(path).encode("utf-8"))[:32]


_VENDOR_DIR_MARKERS = frozenset({".git", "node_modules", "vendor", "__pycache__", ".venv"})
# The two markers A1.50 already excludes unconditionally (`.git/**`,
# `**/__pycache__/**`) regardless of what A1.40 discovered.
_ALWAYS_EXCLUDED_MARKERS = frozenset({".git", "__pycache__"})


def _is_vendor_path(relative: str) -> bool:
    parts = set(relative.split("/"))
    return bool(parts & _VENDOR_DIR_MARKERS)


def _discovered_vendor_dir_markers(vendor_paths: list[str]) -> list[str]:
    """Return which extra vendor markers (beyond the always-excluded ones)
    actually occur among A1.40's discovered vendor/generated file paths.

    Each entry in `vendor_paths` is a full relative file path (e.g.
    "node_modules/lodash/index.js"), so this inspects path segments rather
    than treating an entry itself as a directory name.
    """

    found: set[str] = set()
    for path in vendor_paths:
        found.update(set(path.split("/")) & _VENDOR_DIR_MARKERS)
    return sorted(found - _ALWAYS_EXCLUDED_MARKERS)


def _language_for_suffix(suffix: str) -> str | None:
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cs": "csharp",
        ".sol": "solidity",
    }.get(suffix)


def _metric_profile(
    metric_id: str, *, unit: str | None = None, direction: str | None = None
) -> dict[str, object]:
    """Compatibility facade over the exact, typed metric registry."""

    definition = resolve_metric(
        metric_id,
        declared_unit=unit,
        declared_direction=cast("Any", direction),
    )
    return {
        "aggregation": definition.aggregation,
        "source_types": set(definition.accepted_source_types),
        "canonical_unit": definition.canonical_unit,
        "minimum_samples": definition.minimum_samples,
        "schema_version": definition.schema_version,
    }


def _criterion_id(metric_id: str) -> str:
    """A stable, readable id for one declared criterion.

    Downstream ids are built from this (`evidence-<id>` at A1.71,
    `signal-<id>` at A3.20), so keeping the metric visible is what makes a
    multi-criterion run's signals legible; `ManualCasePayload`'s validator
    already rejects two criteria naming the same metric, so this stays
    unique within a request.
    """

    slug = "".join(char if char.isalnum() else "-" for char in metric_id.lower()).strip("-")
    return slug or "criterion"


def _acceptance_operator(
    direction: Literal["minimize", "maximize", "target"] | None,
) -> Literal["lt", "lte", "eq", "gte", "gt"]:
    if direction == "maximize":
        return "gte"
    if direction == "target":
        return "eq"
    return "lte"


def _workload_defaults_for_metric(metric_id: str | None) -> dict[str, int]:
    profile = _metric_profile(metric_id or "")
    if profile["aggregation"] == "verdict":
        return {"repetitions": 1, "warmup_runs": 0}
    return {"repetitions": 3, "warmup_runs": 1}


__all__ = ["build_a1_registrations", "build_a1_runtime", "build_bound_a1_registrations"]
