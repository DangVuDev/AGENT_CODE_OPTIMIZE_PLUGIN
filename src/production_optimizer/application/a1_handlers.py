from __future__ import annotations

import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

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
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="a1-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_MAX_DISCOVERY_FILES = 500


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
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production A1 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"a1_{node_id.replace('.', '_')}"
    return execute


def build_bound_a1_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import A1_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in A1_NODE_IDS
    }


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "A1.10":
            return _a1_10(state, ports)
        case "A1.20":
            return _a1_20(state, ports)
        case "A1.30":
            return _a1_30(state, ports)
        case "A1.40":
            return _a1_40(state, ports)
        case "A1.50":
            return _a1_50(state, ports)
        case "A1.60":
            return _a1_60(state, ports)
        case "A1.61":
            return _a1_61(state, ports)
        case "A1.62":
            return _a1_62(state, ports)
        case "A1.63":
            return _a1_63(state, ports)
        case "A1.70":
            return _a1_70(state, ports)
        case "A1.71":
            return _a1_71(state, ports)
        case "A1.80":
            return _a1_80(state, ports)
        case "A1.90":
            return _a1_90(state, ports)
        case "A1.95":
            return _a1_95(state, ports)
        case _:
            return NodeExecution()


def _a1_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    payload_ref = _require_ref(state, "ManualCasePayload")
    payload = _read_model(ports, state, payload_ref, ManualCasePayload)
    has_raw = bool((payload.raw_text or "").strip())
    has_structured = payload.structured_request is not None
    if not has_raw and not has_structured:
        raise ValueError("manual case payload must include raw_text or structured_request")

    input_mode = "mixed" if has_raw and has_structured else "raw" if has_raw else "structured"
    envelope = _seal(
        IntakeEnvelope(
            **_base_envelope(state, "IntakeEnvelope", parents=[payload_ref.content_digest]),
            input_mode=cast("Any", input_mode),
            actor_id=payload.actor_id,
            actor_role=payload.actor_role,
            local_path=payload.local_path,
            allowed_root=payload.allowed_root,
            original_payload_digest=payload_ref.content_digest,
        )
    )
    ref = _put_envelope(ports, state, envelope, node_id="A1.10")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    draft = _draft_from_payload(state, payload)
    ref = _put_envelope(ports, state, draft, node_id="A1.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    allowed_root = Path(payload.allowed_root).resolve()
    local_path = Path(payload.local_path).resolve()
    if not local_path.exists():
        raise ValueError(f"local source path does not exist: {local_path}")
    if not local_path.is_relative_to(allowed_root):
        raise ValueError("local source path escapes the allowed root")

    git_revision = _git(local_path, "rev-parse", "HEAD")
    status = _git(local_path, "status", "--porcelain=v1")
    dirty_lines = [line for line in status.splitlines() if line.strip()]
    source = _seal(
        LocalSourceIdentity(
            **_base_envelope(state, "LocalSourceIdentity"),
            repository_id=_repository_id(local_path),
            allowed_root_id=_repository_id(allowed_root),
            canonical_path=str(local_path),
            relative_path=str(local_path.relative_to(allowed_root)) or ".",
            git_revision=git_revision or None,
            dirty=bool(dirty_lines),
            untracked_count=sum(1 for line in dirty_lines if line.startswith("??")),
        )
    )
    ref = _put_envelope(ports, state, source, node_id="A1.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    source = _read_model(
        ports,
        state,
        _require_ref(state, "LocalSourceIdentity"),
        LocalSourceIdentity,
    )
    profile = _discover_project_profile(state, Path(source.canonical_path))
    ref = _put_envelope(ports, state, profile, node_id="A1.40")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    source = _read_model(
        ports,
        state,
        _require_ref(state, "LocalSourceIdentity"),
        LocalSourceIdentity,
    )
    feature_id = draft.feature_id or "unknown-feature"
    include_paths = [source.relative_path if source.relative_path != "." else "*"]
    scope = _seal(
        FeatureScope(
            **_base_envelope(state, "FeatureScope", parents=[draft.content_digest]),
            feature_id=feature_id,
            include_paths=include_paths,
            exclude_paths=[".git/**", "**/__pycache__/**", "node_modules/**"],
            confidence=0.9 if draft.feature_id else 0.0,
            rationale="feature scope came from structured intake or bounded local-path context",
        )
    )
    ref = _put_envelope(ports, state, scope, node_id="A1.50")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    scope = _read_model(ports, state, _require_ref(state, "FeatureScope"), FeatureScope)
    missing: list[str] = []
    objective: Objective | None = None
    if not draft.objective_statement:
        missing.append("objective_statement")
    if scope.confidence <= 0 or scope.feature_id == "unknown-feature":
        missing.append("feature_id")
    if not missing:
        objective = Objective(
            statement=draft.objective_statement or "",
            feature_id=scope.feature_id,
        )
    artifact = _seal(
        CanonicalObjective(
            **_base_envelope(
                state,
                "CanonicalObjective",
                parents=[draft.content_digest, scope.content_digest],
            ),
            objective=objective,
            requester_hypothesis=draft.requester_hypothesis,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.60")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_61(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    missing: list[str] = []
    if not draft.metric_id:
        missing.append("metric_id")
    if draft.direction is None:
        missing.append("direction")
    if draft.target is None:
        missing.append("target")
    if not draft.unit:
        missing.append("unit")
    criteria: list[Criterion] = []
    if not missing:
        criteria.append(
            Criterion(
                criterion_id="primary",
                metric_id=draft.metric_id or "",
                direction=draft.direction or "target",
                target=draft.target or 0.0,
                unit=draft.unit or "",
                weight=1.0,
            )
        )
    artifact = _seal(
        CriterionSet(
            **_base_envelope(state, "CriterionSet", parents=[draft.content_digest]),
            criteria=criteria,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.61")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_62(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    missing: list[str] = []
    if not draft.guardrail_metric_id:
        missing.append("correctness_guardrail")
    guardrails: list[Guardrail] = []
    if not missing:
        guardrails.append(
            Guardrail(
                guardrail_id="correctness",
                metric_id=draft.guardrail_metric_id or "",
                operator="eq",
                threshold=1.0,
                unit="pass",
            )
        )
    artifact = _seal(
        GuardrailSet(
            **_base_envelope(state, "GuardrailSet", parents=[draft.content_digest]),
            guardrails=guardrails,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.62")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_63(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    budget = ExecutionBudget(
        deadline_seconds=300,
        maximum_worker_seconds=180,
        maximum_model_tokens=4000,
        maximum_storage_bytes=50_000_000,
        allowed_analyzers={"tree-sitter", "semgrep"},
    )
    artifact = _seal(
        ExecutionBudgetArtifact(
            **_base_envelope(state, "ExecutionBudgetArtifact"),
            budget=budget,
            policy_version=payload.policy_version,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.63")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    missing: list[str] = []
    if not draft.workload_id:
        missing.append("workload_id")
    if not draft.environment_id:
        missing.append("environment_id")
    workload: WorkloadContract | None = None
    if not missing:
        workload = WorkloadContract(
            workload_id=draft.workload_id or "",
            dataset_id=draft.dataset_id,
            environment_id=draft.environment_id or "",
            command_id=draft.command_id,
            repetitions=3,
            warmup_runs=1,
            concurrency=1,
            cache_state="warm",
        )
    artifact = _seal(
        WorkloadIdentity(
            **_base_envelope(state, "WorkloadIdentity", parents=[draft.content_digest]),
            workload=workload,
            missing_fields=missing,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.70")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_71(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    criteria = _read_model(ports, state, _require_ref(state, "CriterionSet"), CriterionSet)
    guardrails = _read_model(ports, state, _require_ref(state, "GuardrailSet"), GuardrailSet)
    workload = _read_model(ports, state, _require_ref(state, "WorkloadIdentity"), WorkloadIdentity)
    missing = [*criteria.missing_fields, *guardrails.missing_fields, *workload.missing_fields]
    requirements: list[EvidenceRequirement] = []
    if workload.workload is not None:
        for criterion in criteria.criteria:
            requirements.append(
                EvidenceRequirement(
                    requirement_id=f"evidence-{criterion.criterion_id}",
                    criterion_id=criterion.criterion_id,
                    accepted_source_types={"benchmark", "test", "telemetry"},
                    minimum_samples=3,
                    mandatory=True,
                )
            )
        for guardrail in guardrails.guardrails:
            requirements.append(
                EvidenceRequirement(
                    requirement_id=f"guardrail-{guardrail.guardrail_id}",
                    criterion_id=guardrail.guardrail_id,
                    accepted_source_types={"test"},
                    minimum_samples=1,
                    mandatory=True,
                )
            )
    elif "workload_id" not in missing:
        missing.append("workload_id")
    artifact = _seal(
        EvidenceRequirementSet(
            **_base_envelope(
                state,
                "EvidenceRequirementSet",
                parents=[
                    criteria.content_digest,
                    guardrails.content_digest,
                    workload.content_digest,
                ],
            ),
            evidence_requirements=requirements,
            missing_fields=sorted(set(missing)),
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.71")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a1_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    objective = _read_model(
        ports, state, _require_ref(state, "CanonicalObjective"), CanonicalObjective
    )
    criteria = _read_model(ports, state, _require_ref(state, "CriterionSet"), CriterionSet)
    guardrails = _read_model(ports, state, _require_ref(state, "GuardrailSet"), GuardrailSet)
    workload = _read_model(ports, state, _require_ref(state, "WorkloadIdentity"), WorkloadIdentity)
    evidence = _read_model(
        ports, state, _require_ref(state, "EvidenceRequirementSet"), EvidenceRequirementSet
    )
    missing = sorted(
        set(
            objective.missing_fields
            + criteria.missing_fields
            + guardrails.missing_fields
            + workload.missing_fields
            + evidence.missing_fields
        )
    )
    conflicts: list[str] = []
    if any(criterion.target < 0 for criterion in criteria.criteria):
        conflicts.append("criterion target cannot be negative")
    if objective.objective is None:
        conflicts.append("objective is not canonicalized")
    if not criteria.criteria:
        conflicts.append("at least one primary criterion is required")
    if not guardrails.guardrails:
        conflicts.append("at least one correctness guardrail is required")
    if workload.workload is None:
        conflicts.append("workload identity is required")
    if not evidence.evidence_requirements:
        conflicts.append("evidence requirements are required")
    passed = not missing and not conflicts
    report = _seal(
        A1QualityReport(
            **_base_envelope(
                state,
                "A1QualityReport",
                parents=[
                    objective.content_digest,
                    criteria.content_digest,
                    guardrails.content_digest,
                    workload.content_digest,
                    evidence.content_digest,
                ],
            ),
            passed=passed,
            missing_fields=missing,
            conflicts=conflicts,
            policy_version=payload.policy_version,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="A1.80")
    if passed:
        return NodeExecution(updates={"artifact_refs": [ref]})

    case_id = _required_state_str(state, "case_id")
    interrupt = InterruptEnvelope(
        interrupt_id=f"{case_id}-A1-CLARIFICATION",
        case_id=case_id,
        thread_id=_required_state_str(state, "thread_id"),
        stage="A1.80",
        artifact_digest=report.content_digest,
        allowed_decisions=["revise", "reject"],
        required_actor_role=payload.actor_role,
        policy_version=payload.policy_version,
        issued_at=_now(),
        expires_at=_now() + timedelta(hours=24),
    )
    return NodeExecution(
        route=NodeRoute.CLARIFICATION,
        updates={"artifact_refs": [ref], "pending_interrupt": interrupt},
    )


def _a1_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    quality = _read_model(ports, state, _require_ref(state, "A1QualityReport"), A1QualityReport)
    objective = _read_model(
        ports, state, _require_ref(state, "CanonicalObjective"), CanonicalObjective
    )
    criteria = _read_model(ports, state, _require_ref(state, "CriterionSet"), CriterionSet)
    guardrails = _read_model(ports, state, _require_ref(state, "GuardrailSet"), GuardrailSet)
    workload = _read_model(ports, state, _require_ref(state, "WorkloadIdentity"), WorkloadIdentity)
    evidence = _read_model(
        ports, state, _require_ref(state, "EvidenceRequirementSet"), EvidenceRequirementSet
    )
    budget = _read_model(
        ports,
        state,
        _require_ref(state, "ExecutionBudgetArtifact"),
        ExecutionBudgetArtifact,
    )
    fingerprint = _request_fingerprint(
        state,
        objective=objective,
        criteria=criteria,
        guardrails=guardrails,
        workload=workload,
        evidence=evidence,
        budget=budget,
    )
    approval: ApprovalBinding | None = None
    route = "continue"
    reasons: list[str] = []
    if not quality.passed:
        route = "rejected"
        reasons = [*quality.missing_fields, *quality.conflicts]
    elif payload.actor_role not in {"owner", "approver", "platform_owner"}:
        route = "approval"
        reasons = ["actor role requires approval"]
    else:
        approval = ApprovalBinding(
            approval_id=f"{_required_state_str(state, 'case_id')}-A1-APPROVAL",
            actor_id=payload.actor_id,
            actor_role=payload.actor_role,
            decision="approve",
            artifact_digest=fingerprint,
            policy_version=payload.policy_version,
        )
    artifact = _seal(
        A1ApprovalDecision(
            **_base_envelope(
                state,
                "A1ApprovalDecision",
                parents=[
                    quality.content_digest,
                    objective.content_digest,
                    criteria.content_digest,
                    guardrails.content_digest,
                    workload.content_digest,
                    evidence.content_digest,
                    budget.content_digest,
                ],
            ),
            approved=approval is not None,
            approval=approval,
            decision_route=cast("Any", route),
            reasons=reasons,
            policy_version=payload.policy_version,
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.90")
    if route == "continue":
        return NodeExecution(updates={"artifact_refs": [ref]})
    if route == "approval":
        interrupt = InterruptEnvelope(
            interrupt_id=f"{_required_state_str(state, 'case_id')}-A1-APPROVAL",
            case_id=_required_state_str(state, "case_id"),
            thread_id=_required_state_str(state, "thread_id"),
            stage="A1.90",
            artifact_digest=artifact.content_digest,
            allowed_decisions=["approve", "reject"],
            required_actor_role="owner",
            policy_version=payload.policy_version,
            issued_at=_now(),
            expires_at=_now() + timedelta(hours=24),
        )
        return NodeExecution(
            route=NodeRoute.APPROVAL,
            updates={"artifact_refs": [ref], "pending_interrupt": interrupt},
        )
    return NodeExecution(route=NodeRoute.REJECTED, updates={"artifact_refs": [ref]})


def _a1_95(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    request = _optimization_request(state, ports)
    ref = _put_envelope(ports, state, request, node_id="A1.95")
    return NodeExecution(updates={"request_ref": ref, "artifact_refs": [ref]})


def _draft_from_payload(state: OptimizationState, payload: ManualCasePayload) -> RawRequestDraft:
    structured = payload.structured_request or {}
    raw = payload.raw_text or ""
    objective = _nested_str(structured, "objective", "statement") or _str(structured, "objective")
    feature_id = _nested_str(structured, "objective", "feature_id") or _str(
        structured, "feature_id"
    )
    metric_id = _first_criterion_value(structured, "metric_id")
    direction = _first_criterion_value(structured, "direction")
    target = _first_criterion_value(structured, "target")
    unit = _first_criterion_value(structured, "unit")

    if not objective and raw.strip():
        objective = raw.strip()[:4000]
    if not feature_id:
        feature_id = "local-feature" if raw.strip() else None
    if not metric_id and "latency" in raw.lower():
        metric_id = "p95_latency_ms"
        direction = direction or "minimize"
        unit = unit or "ms"
    if target is None and metric_id:
        target = 100.0

    unresolved: list[str] = []
    if not objective:
        unresolved.append("objective_statement")
    if not feature_id:
        unresolved.append("feature_id")
    if not metric_id:
        unresolved.append("metric_id")
    if direction not in {"minimize", "maximize", "target"}:
        unresolved.append("direction")
        direction = None
    if target is None:
        unresolved.append("target")
    if not unit:
        unresolved.append("unit")

    workload_id = _nested_str(structured, "workload", "workload_id") or "local-workload"
    environment_id = _nested_str(structured, "workload", "environment_id") or "local-env"
    command_id = _nested_str(structured, "workload", "command_id")

    draft = RawRequestDraft(
        **_base_envelope(state, "RawRequestDraft"),
        origin=Origin.MANUAL,
        objective_statement=objective,
        feature_id=feature_id,
        metric_id=cast("str | None", metric_id),
        direction=cast("Any", direction),
        target=float(target) if isinstance(target, int | float) else None,
        unit=cast("str | None", unit),
        guardrail_metric_id="unit_tests",
        workload_id=workload_id,
        dataset_id=_nested_str(structured, "workload", "dataset_id"),
        environment_id=environment_id,
        command_id=command_id,
        extraction_confidence=1.0 if payload.structured_request else 0.55,
        unresolved_fields=unresolved,
        requester_hypothesis=_str(structured, "requester_hypothesis"),
    )
    return _seal(draft)


def _discover_project_profile(state: OptimizationState, root: Path) -> ProjectProfile:
    suffix_counts: dict[str, int] = {}
    manifests: list[str] = []
    test_roots: set[str] = set()
    vendor: list[str] = []
    file_count = 0
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
        generated_or_vendor_paths=sorted(vendor)[:100],
        file_count=file_count,
        discovery_truncated=truncated,
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
        objective=objective.objective,
        criteria=criteria.criteria,
        guardrails=guardrails.guardrails,
        workload=workload.workload,
        evidence_requirements=evidence.evidence_requirements,
        budget=budget.budget,
        approval=approval.approval,
        request_fingerprint=fingerprint,
    )
    return _seal(request)


def _request_fingerprint(
    state: OptimizationState,
    *,
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


def _is_vendor_path(relative: str) -> bool:
    parts = set(relative.split("/"))
    return bool(parts & {".git", "node_modules", "vendor", "__pycache__", ".venv"})


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


def _str(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _nested_str(mapping: Mapping[str, Any], parent: str, key: str) -> str | None:
    value = mapping.get(parent)
    if not isinstance(value, Mapping):
        return None
    return _str(cast("Mapping[str, Any]", value), key)


def _first_criterion_value(mapping: Mapping[str, Any], key: str) -> object | None:
    criteria = mapping.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return None
    first = cast("list[object]", criteria)[0]
    if not isinstance(first, Mapping):
        return None
    return cast("Mapping[str, Any]", first).get(key)


__all__ = ["build_a1_registrations", "build_a1_runtime", "build_bound_a1_registrations"]
