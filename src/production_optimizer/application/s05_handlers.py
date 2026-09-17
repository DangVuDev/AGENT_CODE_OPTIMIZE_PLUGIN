"""Production handlers for S05 (Controlled Remeasurement) -- measures the
verified treatment against A2's own real baseline while changing exactly one
logical variable (see docs/project-blueprint/shared-workflow/
05-controlled-remeasurement.md).

Reuses S03's own isolated, already-patched workspace exactly like S04 does
(see `s03_handlers._s03_90`'s docstring) -- no second isolation boundary.
Remeasurement itself reuses the *exact* exit-code-as-metric-value convention
A2.60/61 already established (`f"{kind}_command_result"`, `unit="exit_code"`)
by literally running the repository's own already-detected command again in
the reused workspace via `s04_worker_capabilities.build_s04_capabilities`,
merged with `s05_worker_capabilities.build_s05_capabilities` for a real
pytest-benchmark round trip when the manifest actually has a "benchmark"
command. A criterion whose `metric_id` cannot be resolved to any real,
detected command is honestly left unmeasured (never a fabricated number) and
becomes a real, material incomparability at S05.70 -- consistent with this
codebase's other "honest gap" boundaries (B1.32-35, S04.60).

BR-05-004 (incomparable measurements cannot support KEEP or REVERT) is
enforced by routing S05.70 `rejected` on any material incomparability; this
milestone does not implement the doc's "Measurement retry" bounded sub-loop
for a transient incomparability -- a deliberate, acknowledged scope boundary
(the outer graph simply halts, like every other early `rejected` gate in
this codebase), not a hidden gap.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.application.s04_worker_capabilities import build_s04_capabilities
from production_optimizer.application.s05_worker_capabilities import build_s05_capabilities
from production_optimizer.contracts.a1 import Criterion, OptimizationRequest
from production_optimizer.contracts.a2 import (
    BaselineSnapshot,
    ComparabilityReport,
    DimensionVerdict,
    MetricAggregate,
    RepositoryManifest,
    SourceSnapshot,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.platform import WorkerJob
from production_optimizer.contracts.s02 import ExecutionPlan
from production_optimizer.contracts.s03 import PatchArtifact
from production_optimizer.contracts.s05 import (
    EffectResult,
    IsolationReport,
    IsolationViolation,
    Measurement,
    StatisticalReport,
)
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="s05-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_POLICY_VERSION = "s05-comparability-v1"
_RESOLVABLE_KINDS = {"build", "lint", "type", "unit", "integration", "security", "benchmark"}
_MAX_CHECK_SECONDS = 120.0

_S05_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "S05.30": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S05.70": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _S05_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-workflow-s05",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="s05-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=300,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/05-controlled-remeasurement.md",
        slo="S05 node completes within the experiment budget",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production S05 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"s05_{node_id.replace('.', '_')}"
    return execute


def build_s05_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import S05_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in S05_NODE_IDS
    }


def build_s05_runtime(*, ports: NodePorts) -> NodeRuntime:
    return NodeRuntime(build_s05_registrations(), ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "S05.10":
            return _s05_10(state, ports)
        case "S05.20":
            return _s05_20(state, ports)
        case "S05.30":
            return _s05_30(state, ports)
        case "S05.40":
            return _s05_40(state, ports)
        case "S05.50":
            return _s05_50(state, ports)
        case "S05.60":
            return _s05_60(state, ports)
        case "S05.70":
            return _s05_70(state, ports)
        case "S05.80":
            return _s05_80(state, ports)
        case "S05.90":
            return _s05_90(state, ports)
        case _:
            return NodeExecution()


def _active_phase_id(state: OptimizationState) -> str:
    value = state.get("s03_active_phase_id")
    if not isinstance(value, str) or not value:
        raise ValueError("S05 state is missing s03_active_phase_id (S03/S04 must run first)")
    return value


def _pass_number(state: OptimizationState) -> int:
    return state.get("s03_revision_attempts", 0) or 0


def _s03_patch_stage(state: OptimizationState) -> str:
    return f"S03.80-{_active_phase_id(state)}-pass{_pass_number(state)}"


def _s05_stage(node_id: str, state: OptimizationState) -> str:
    return f"{node_id}-{_active_phase_id(state)}-pass{_pass_number(state)}"


def _s05_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Pin the same workload/environment/aggregation rules A2 already froze
    in `BaselineSnapshot` -- no new protocol invented, BR-05-003's outlier/
    stopping rules are fixed *before* this pass's treatment run below."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    baseline = cast("BaselineSnapshot", _read_required(ports, state, "BaselineSnapshot"))
    protocol = {
        "workload_id": baseline.workload_id,
        "environment_id": baseline.environment_id,
        "repetitions": request.workload.repetitions,
        "warmup_runs": request.workload.warmup_runs,
        "concurrency": request.workload.concurrency,
        "cache_state": request.workload.cache_state,
    }
    return NodeExecution(updates={"s05_protocol": protocol})


def _s05_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real isolation audit grounded in S03's own `PatchArtifact.
    scope_report`/`changed_files` -- a dependency-manifest file among them is
    a real BR-05-001 violation, never a second, independent git diff against
    a workspace S04 may have already cleaned up on a real pass.

    Deliberate scope boundary, not an oversight: this audit can only detect
    drift that touches a *tracked* file the patch or manifest already know
    about. True environment drift that never touches a tracked file at all
    -- e.g. a CI runner silently upgrading an untracked system package, or
    an environment variable changing between A2's baseline run and this
    remeasurement pass -- has no real, in-repo signal this codebase can
    check without new infrastructure (mirrors B1.32-35's historical-metrics
    gap: an honest, acknowledged absence, never a fabricated pass). What IS
    in scope and checked below: if the repository's own tracked
    dependency-manifest files (`RepositoryManifest.manifest_files`, e.g.
    requirements.txt/pyproject.toml/lockfiles) have drifted in *content*
    from what `SourceSnapshot` recorded at A2.30 time -- even if the patch
    itself never touched them -- that is real, closable drift (e.g. a
    CI-side `pip install --upgrade` between baseline and remeasurement) and
    is real, closable drift this audit can actually ground in a digest
    comparison.
    """

    patch_stage = _s03_patch_stage(state)
    patch_ref = _require_stage_ref(state, patch_stage, "PatchArtifact")
    patch = _read_model(ports, state, patch_ref, PatchArtifact)
    manifest = cast("RepositoryManifest", _read_required(ports, state, "RepositoryManifest"))
    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    workspace_root = Path(cast("str", workspace.get("path", "")))

    violations: list[IsolationViolation] = []
    if not patch.scope_report.in_scope:
        violations.append(
            IsolationViolation(
                kind="scope_violation", detail="S03's own scope report recorded a violation"
            )
        )
    for path in sorted(set(patch.changed_files) & set(manifest.manifest_files)):
        violations.append(
            IsolationViolation(kind="dependency_drift", detail=f"{path} changed with the patch")
        )

    digest_by_path = {f.relative_path: f.content_digest for f in snapshot.files}
    for path in sorted(manifest.manifest_files):
        expected_digest = digest_by_path.get(path)
        if expected_digest is None or path in patch.changed_files:
            # Not tracked at A2.30 time, or already covered by the
            # dependency_drift check above -- don't double-flag the same
            # real cause.
            continue
        candidate = workspace_root / path
        if not candidate.is_file():
            continue
        actual_digest = sha256_digest(candidate.read_bytes())
        if actual_digest != expected_digest:
            violations.append(
                IsolationViolation(
                    kind="dependency_drift",
                    detail=(
                        f"{path} content changed outside the patch since A2.30's "
                        "snapshot -- real environment/dependency drift"
                    ),
                )
            )

    isolated = not violations
    report = _seal(
        IsolationReport(
            **_stage_envelope(state, _s05_stage("S05.20", state), "IsolationReport"),
            patch_digest=patch_ref.content_digest,
            isolated=isolated,
            violations=violations,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="S05.20")
    return NodeExecution(
        updates={"artifact_refs": [ref], "s05_isolation": {"isolated": isolated}}
    )


def _s05_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Confirm S03's real, already-patched workspace is still there to run
    the remeasurement workload against."""

    del ports
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    root = Path(cast("str", workspace.get("path", "")))
    ready = root.exists() and root.is_dir()
    route = NodeRoute.CONTINUE if ready else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"s05_isolation": _merge_isolation(state, ready)})


def _merge_isolation(state: OptimizationState, environment_ready: bool) -> dict[str, Any]:
    isolation = dict(cast("dict[str, Any]", state.get("s05_isolation") or {}))
    isolation["environment_ready"] = environment_ready
    return isolation


def _resolve_kind(criterion: Criterion, manifest: RepositoryManifest) -> str | None:
    kind = criterion.metric_id.removesuffix("_command_result")
    if kind == criterion.metric_id or kind not in _RESOLVABLE_KINDS:
        return None
    if not any(command.kind == kind for command in manifest.commands):
        return None
    return kind


def _s05_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real workload execution: reruns each criterion's own already-detected
    repository command in S03's real, already-patched workspace -- the exact
    same exit-code/benchmark-round convention A2.60/61 established, applied
    a second time against the treatment instead of the pre-patch baseline."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    manifest = cast("RepositoryManifest", _read_required(ports, state, "RepositoryManifest"))
    manifest_ref = _require_ref(state, "RepositoryManifest")
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    workspace_root = Path(cast("str", workspace["path"]))
    case_id = _required_state_str(state, "case_id")
    tenant_id = _required_state_str(state, "tenant_id")

    broker = LocalWorkerBroker(
        capabilities={
            **build_s04_capabilities(
                ports.artifacts, tenant_id=tenant_id, workspace_root=workspace_root
            ),
            **build_s05_capabilities(
                ports.artifacts, tenant_id=tenant_id, workspace_root=workspace_root
            ),
        }
    )
    aggregates: dict[str, dict[str, Any]] = {}
    unmeasurable: list[str] = []
    try:
        for criterion in request.criteria:
            kind = _resolve_kind(criterion, manifest)
            if kind is None:
                unmeasurable.append(criterion.criterion_id)
                continue
            aggregate = _remeasure_one(
                ports, broker, case_id, tenant_id, manifest_ref, criterion, kind
            )
            aggregates[criterion.criterion_id] = aggregate.model_dump(mode="json")
    finally:
        broker.close()
        # This is the real, final consumer of S03's isolated workspace --
        # S04.90 deliberately stopped cleaning it up on a pass once S05
        # needed to rerun the workload here (see its own docstring). Cleaned
        # up unconditionally (measurable or not) since nothing later in S05
        # touches the filesystem, only these already-produced aggregates.
        _cleanup_workspace(workspace)

    return NodeExecution(
        updates={
            "s05_treatment_aggregates": {
                "by_criterion": aggregates,
                "unmeasurable_criterion_ids": unmeasurable,
            }
        }
    )


def _remeasure_one(
    ports: NodePorts,
    broker: LocalWorkerBroker,
    case_id: str,
    tenant_id: str,
    manifest_ref: ArtifactRef,
    criterion: Criterion,
    kind: str,
) -> MetricAggregate:
    job_id = f"{case_id}-S05-{criterion.criterion_id}"
    receipt = broker.submit(
        WorkerJob(
            job_id=job_id, case_id=case_id, node_id="S05", idempotency_key=job_id,
            input_refs=[manifest_ref], capability=kind, timeout_seconds=int(_MAX_CHECK_SECONDS),
        )
    )
    if not receipt.accepted:
        raise RuntimeError(f"S05 remeasurement job {job_id!r} was not accepted")

    output_ref = _await_worker_result(broker, job_id=job_id, timeout_seconds=_MAX_CHECK_SECONDS)
    if output_ref is None:
        raise RuntimeError(f"S05 remeasurement job {job_id!r} timed out")

    raw = ports.artifacts.read(tenant_id=tenant_id, ref=output_ref)
    payload = TypeAdapter(dict[str, Any]).validate_json(raw)

    if kind == "benchmark":
        rounds = cast("list[dict[str, Any]]", payload.get("benchmark_rounds") or [])
        values = [float(round_data["value"]) for round_data in rounds]
        if not values:
            raise RuntimeError(f"benchmark for {criterion.criterion_id!r} produced no rounds")
        return MetricAggregate(
            metric_id=criterion.metric_id, unit=criterion.unit,
            sample_ids=[f"{job_id}-{i}" for i in range(1, len(values) + 1)], count=len(values),
            minimum=min(values), maximum=max(values), mean=sum(values) / len(values),
        )

    exit_code = float(cast("int", payload.get("exit_code", -1)))
    return MetricAggregate(
        metric_id=criterion.metric_id, unit="exit_code", sample_ids=[f"{job_id}-1"], count=1,
        minimum=exit_code, maximum=exit_code, mean=exit_code,
    )


def _await_worker_result(
    broker: LocalWorkerBroker, *, job_id: str, timeout_seconds: float
) -> ArtifactRef | None:
    deadline = time.monotonic() + min(timeout_seconds, 30.0)
    while time.monotonic() < deadline:
        ref = broker.reconcile(job_id=job_id, idempotency_key=job_id)
        if ref is not None:
            return ref
        time.sleep(0.05)
    return None


def _s05_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Preserve observations: BR-05-002 is satisfied by S05.40's raw worker
    output already being content-addressed and immutable the moment it was
    written -- this node's real work is verifying every one of this pass's
    treatment aggregates is still readable and digest-correct, not
    re-storing anything."""

    treatment = cast("dict[str, Any]", state.get("s05_treatment_aggregates") or {})
    by_criterion = cast("dict[str, Any]", treatment.get("by_criterion") or {})
    preserved = sorted(by_criterion)
    return NodeExecution(
        updates={
            "s05_treatment_aggregates": {**treatment, "preserved_criterion_ids": preserved},
        }
    )


def _s05_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real, deterministic sample-quality check: every A1 criterion must
    have been measured (BR-05-005) with at least its required sample count
    (`EvidenceRequirement.minimum_samples`) -- a criterion S05.40 could not
    resolve to any real command is a real quality failure, not silently
    dropped. Also fails a criterion whose metric_id has no matching
    `BaselineSnapshot` aggregate at all: without a real baseline to compare
    against, `_s05_80` has nothing honest to compute an effect from (a
    fabricated zero-baseline "improvement" would be worse than an honest
    failure here)."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    baseline = cast("BaselineSnapshot", _read_required(ports, state, "BaselineSnapshot"))
    baseline_metric_ids = {aggregate.metric_id for aggregate in baseline.aggregates}
    treatment = cast("dict[str, Any]", state.get("s05_treatment_aggregates") or {})
    by_criterion = cast("dict[str, Any]", treatment.get("by_criterion") or {})
    unmeasurable = set(cast("list[str]", treatment.get("unmeasurable_criterion_ids") or []))
    requirements = {r.criterion_id: r.minimum_samples for r in request.evidence_requirements}

    reasons: list[str] = []
    for criterion in request.criteria:
        if criterion.criterion_id in unmeasurable:
            reasons.append(f"{criterion.criterion_id}: no real command resolved this pass")
            continue
        if criterion.metric_id not in baseline_metric_ids:
            reasons.append(
                f"{criterion.criterion_id}: no baseline aggregate for metric "
                f"{criterion.metric_id!r} -- not comparable"
            )
            continue
        aggregate_raw = by_criterion.get(criterion.criterion_id)
        minimum_samples = requirements.get(criterion.criterion_id, 1)
        if aggregate_raw is None or aggregate_raw.get("count", 0) < minimum_samples:
            reasons.append(f"{criterion.criterion_id}: fewer than {minimum_samples} real samples")

    quality = {"passed": not reasons, "reasons": reasons}
    return NodeExecution(updates={"s05_quality": quality})


def _s05_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real, deterministic comparability gate (BR-05-004): every material
    dimension must hold, or this pass cannot support KEEP or REVERT and the
    outer graph halts here (this milestone's acknowledged scope boundary --
    no bounded incomparability-retry sub-loop yet, see module docstring)."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    baseline = cast("BaselineSnapshot", _read_required(ports, state, "BaselineSnapshot"))
    protocol = cast("dict[str, Any]", state.get("s05_protocol") or {})
    isolation = cast("dict[str, Any]", state.get("s05_isolation") or {})
    quality = cast("dict[str, Any]", state.get("s05_quality") or {})

    dimensions = [
        DimensionVerdict(
            dimension="isolation", comparable=bool(isolation.get("isolated")),
            baseline_value="isolated", expected_value="isolated", material=True,
            reason="S03's patch scope/sanitation must be the only real difference",
        ),
        DimensionVerdict(
            dimension="sample_quality", comparable=bool(quality.get("passed")),
            baseline_value="sufficient", expected_value="sufficient", material=True,
            reason="; ".join(cast("list[str]", quality.get("reasons") or [])) or "sufficient",
        ),
        DimensionVerdict(
            dimension="workload_id",
            comparable=protocol.get("workload_id") == request.workload.workload_id,
            baseline_value=str(baseline.workload_id),
            expected_value=str(protocol.get("workload_id")), material=True,
            reason="workload identity must match the frozen A2 protocol",
        ),
        DimensionVerdict(
            dimension="environment_id",
            comparable=protocol.get("environment_id") == baseline.environment_id,
            baseline_value=str(baseline.environment_id),
            expected_value=str(protocol.get("environment_id")), material=True,
            reason="environment identity must match the frozen A2 protocol",
        ),
    ]
    comparable = all(not d.material or d.comparable for d in dimensions)
    report = _seal(
        ComparabilityReport(
            **_stage_envelope(state, _s05_stage("S05.70", state), "ComparabilityReport"),
            comparable=comparable,
            dimensions=dimensions,
            policy_version=_POLICY_VERSION,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="S05.70")
    route = NodeRoute.CONTINUE if comparable else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


def _s05_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Compute each criterion's raw effect size against A2's real baseline
    aggregate -- no business-significance judgment here (that is A1/S06.20
    policy, per the doc)."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    baseline = cast("BaselineSnapshot", _read_required(ports, state, "BaselineSnapshot"))
    treatment = cast("dict[str, Any]", state.get("s05_treatment_aggregates") or {})
    by_criterion = cast("dict[str, Any]", treatment.get("by_criterion") or {})
    baseline_by_metric = {a.metric_id: a for a in baseline.aggregates}

    effects: list[EffectResult] = []
    for criterion in request.criteria:
        aggregate_raw = by_criterion.get(criterion.criterion_id)
        if aggregate_raw is None:
            continue
        baseline_aggregate = baseline_by_metric.get(criterion.metric_id)
        if baseline_aggregate is None:
            # No real baseline to compare against -- honestly unmeasurable,
            # never a fabricated zero-baseline effect. S05.60 already fails
            # quality (and S05.70's comparability gate already halts the
            # pass) for this same reason, so skipping this criterion's
            # EffectResult here is belt-and-suspenders consistency, not the
            # sole enforcement.
            continue
        aggregate = MetricAggregate.model_validate(aggregate_raw)
        baseline_mean = baseline_aggregate.mean
        absolute_change = aggregate.mean - baseline_mean
        relative_change = absolute_change / baseline_mean if baseline_mean else None
        effects.append(
            EffectResult(
                criterion_id=criterion.criterion_id, metric_id=criterion.metric_id,
                baseline_mean=baseline_mean, treatment_mean=aggregate.mean,
                absolute_change=absolute_change, relative_change=relative_change,
                sample_count=aggregate.count,
            )
        )

    report = _seal(
        StatisticalReport(
            **_stage_envelope(state, _s05_stage("S05.80", state), "StatisticalReport"),
            effects=effects,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="S05.80")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _s05_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The only node that seals `Measurement` -- binds baseline, patch and
    every S05 report by digest, phase-and-pass scoped like S03.80/S04.90."""

    baseline_ref = _require_ref(state, "BaselineSnapshot")
    patch_ref = _require_stage_ref(state, _s03_patch_stage(state), "PatchArtifact")
    isolation_ref = _require_stage_ref(state, _s05_stage("S05.20", state), "IsolationReport")
    comparability_ref = _require_stage_ref(
        state, _s05_stage("S05.70", state), "ComparabilityReport"
    )
    statistical_ref = _require_stage_ref(state, _s05_stage("S05.80", state), "StatisticalReport")

    measurement = _seal(
        Measurement(
            **_stage_envelope(state, _s05_stage("S05.90", state), "Measurement"),
            baseline_digest=baseline_ref.content_digest,
            patch_digest=patch_ref.content_digest,
            isolation_report_digest=isolation_ref.content_digest,
            comparability_report_digest=comparability_ref.content_digest,
            statistical_report_digest=statistical_ref.content_digest,
            phase_id=_active_phase_id(state),
        )
    )
    ref = _put_envelope(ports, state, measurement, node_id="S05.90")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# Shared helpers (mirrors s03_handlers.py/s04_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "OptimizationRequest": cast("type[ArtifactEnvelope]", OptimizationRequest),
    "BaselineSnapshot": cast("type[ArtifactEnvelope]", BaselineSnapshot),
    "RepositoryManifest": cast("type[ArtifactEnvelope]", RepositoryManifest),
    "SourceSnapshot": cast("type[ArtifactEnvelope]", SourceSnapshot),
    "ExecutionPlan": cast("type[ArtifactEnvelope]", ExecutionPlan),
}


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> Any:
    ref = _require_ref(state, artifact_type)
    return _read_model(ports, state, ref, _MODEL_BY_TYPE[artifact_type])


def _put_envelope(
    ports: NodePorts, state: OptimizationState, envelope: ArtifactEnvelope, *, node_id: str
) -> ArtifactRef:
    content = canonical_json(envelope.model_dump(mode="json", exclude={"content_digest"}))
    generic_ref = ports.artifacts.put_json(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=envelope.content_digest,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:{node_id}:"
            f"{envelope.artifact_type}:{envelope.artifact_id}"
        ),
    )
    return ArtifactRef(
        artifact_type=envelope.artifact_type,
        schema_version=envelope.schema_version,
        artifact_id=envelope.artifact_id,
        content_digest=envelope.content_digest,
        uri=generic_ref.uri,
    )


def _read_model[T](
    ports: NodePorts, state: OptimizationState, ref: ArtifactRef, model: type[T]
) -> T:
    content = ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=ref)
    raw = TypeAdapter(dict[str, Any]).validate_json(content)
    if issubclass(cast("type[Any]", model), ArtifactEnvelope):
        raw.setdefault("content_digest", ref.content_digest)
    return cast("Any", model).model_validate(raw)


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
        "policy_versions": {"s05": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _stage_artifact_id(case_id: str, node_id: str, artifact_type: str) -> str:
    return f"{case_id}-{node_id}-{artifact_type}"


def _stage_envelope(state: OptimizationState, node_id: str, artifact_type: str) -> dict[str, Any]:
    envelope = _base_envelope(state, artifact_type)
    envelope["artifact_id"] = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, artifact_type
    )
    return envelope


def _require_stage_ref(state: OptimizationState, node_id: str, artifact_type: str) -> ArtifactRef:
    artifact_id = _stage_artifact_id(_required_state_str(state, "case_id"), node_id, artifact_type)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required {artifact_type} produced by {node_id}")


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    ref = _try_ref(state, artifact_type)
    if ref is None:
        raise ValueError(f"missing required artifact ref: {artifact_type}")
    return ref


def _try_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef | None:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    return None


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"S05 state is missing required field {key!r}")
    return value


def _cleanup_workspace(workspace: dict[str, Any]) -> None:
    """Mirrors `s03_handlers._cleanup_workspace` exactly (duplicated, not
    imported, per this codebase's self-contained-handler-file convention).
    S05.40 is the new real, final consumer of S03's isolated workspace -- see
    that node's docstring for why S04.90 no longer cleans it up itself."""

    path = workspace.get("path")
    if not path:
        return
    workspace_dir = Path(cast("str", path))
    if workspace.get("isolation") == "git_worktree":
        source_root = workspace.get("source_root")
        if source_root and workspace_dir.exists():
            _git(
                Path(cast("str", source_root)), "worktree", "remove", "--force", str(workspace_dir)
            )
    shutil.rmtree(workspace_dir.parent, ignore_errors=True)


def _git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


__all__ = ["build_s05_registrations", "build_s05_runtime"]
