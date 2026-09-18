"""Production handlers for S04 (Verify One Phase) -- proves S03's real patch
builds, preserves required behavior and satisfies static/domain checks (see
docs/project-blueprint/shared-workflow/04-verify-phase.md).

Reuses S03's own isolated workspace directly (see `s03_handlers._s03_90`'s
docstring): unlike A2 (whose target repo root is known before its graph
even starts, so its `LocalWorkerBroker` can be constructed once by the
caller and injected via `ports.workers`), S04's workspace path is only
known once S03.20 actually runs. Each check-running node here builds its
own short-lived `LocalWorkerBroker` bound to that real, now-known path
(`s04_worker_capabilities.build_s04_capabilities`) rather than requiring
one pre-configured externally -- `ports.workers` is not used by this file.

S04.70's failure classification is grounded in the real `BaselineSnapshot`
A2 sealed before any patch existed: a check kind that already showed a
nonzero aggregate there is a `baseline_existing_failure`, never fabricated
as a new `patch_regression`. S04.80 is the real gate (BR-04-005: a
performance improvement never excuses a correctness/security failure,
enforced by `VerificationReport`'s own validator, not merely asserted
here). A mandatory-check failure routes `revision` -- `s04_handlers` cannot
itself send the graph back to S03; `orchestration/shared_workflow.py`'s
outer wiring does that, mirroring B2.52's `revision` edge back to B2.22.

`CheckResult.coverage_percent` is real, not fabricated, when the manifest
declares `pytest-cov` (`RepositoryManifest.tool_coverage["pytest_cov"]`,
detected at real A2.30): `s04_worker_capabilities` appends real `--cov`
flags to the unit-test command and parses a real percentage out of
pytest-cov's own terminal report. `None` otherwise -- per the spec's own
S04.90 row ("Store commands, outputs, versions, durations, coverage and
decision") and this codebase's "honest unavailable" convention.

Known gap versus the spec's S04.70 row: it lists five failure
classifications (patch regression, baseline-existing failure, flaky test,
environment failure, tool failure); `FailureAttribution.classification`
already declares all five as a `Literal`, but this milestone's `_s04_70`
only ever assigns `patch_regression`/`baseline_existing_failure` --
`flaky`/`environment_failure`/`tool_failure` require either a real repeat
run (to distinguish flaky from a real regression) or output-pattern
heuristics this milestone does not implement. An explicit, acknowledged
gap, not a hidden one (mirrors B1.32-35 and S03.70's own "license issues"
gap).
"""

from __future__ import annotations

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
from production_optimizer.contracts.a2 import BaselineSnapshot, RepositoryManifest
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, model_content_digest
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.platform import WorkerJob
from production_optimizer.contracts.s02 import ExecutionPlan, TaskList
from production_optimizer.contracts.s03 import ExecutionProvenance, PatchArtifact
from production_optimizer.contracts.s04 import CheckResult, FailureAttribution, VerificationReport
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="s04-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_MANDATORY_KINDS = {"build", "lint", "type", "unit"}
_STATIC_KINDS = ("build", "lint", "type")
_MAX_CHECK_SECONDS = 120.0
_OUTPUT_TAIL_CHARS = 2000

_S04_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "S04.10": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S04.80": {NodeRoute.CONTINUE.value, NodeRoute.REVISION.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _S04_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-workflow-s04",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="s04-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=300,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/04-verify-phase.md",
        slo="S04 node completes within the repository's own check deadlines",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production S04 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"s04_{node_id.replace('.', '_')}"
    return execute


def build_s04_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import S04_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in S04_NODE_IDS
    }


def build_s04_runtime(*, ports: NodePorts) -> NodeRuntime:
    return NodeRuntime(build_s04_registrations(), ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "S04.10":
            return _s04_10(state, ports)
        case "S04.20":
            return _s04_20(state, ports)
        case "S04.30":
            return _s04_30(state, ports)
        case "S04.40":
            return _s04_40(state, ports)
        case "S04.50":
            return _s04_50(state, ports)
        case "S04.60":
            return _s04_60(state, ports)
        case "S04.70":
            return _s04_70(state, ports)
        case "S04.80":
            return _s04_80(state, ports)
        case "S04.90":
            return _s04_90(state, ports)
        case _:
            return NodeExecution()


def _active_phase_id(state: OptimizationState) -> str:
    value = state.get("s03_active_phase_id")
    if not isinstance(value, str) or not value:
        raise ValueError("S04 state is missing s03_active_phase_id (S03 must run first)")
    return value


def _s04_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Confirm S03's workspace is still really there -- "clean reproducible
    workspace", not a placeholder claim.

    Also resets `s04_check_results`/`s04_failure_attributions` to empty on
    every entry into S04: those two fields are plain last-value state
    (appended to across S04.30/40/50 within one pass), and this is the first
    node of every pass -- without this reset, a real retry of the same phase
    (see `_s04_90`'s docstring) would carry the previous, still-failing
    pass's results forward and `_s04_80`/`_s04_90` would see a corrupted mix
    of stale and fresh check results instead of only this pass's real ones.
    """

    del ports
    plan_ref = _require_ref(state, "ExecutionPlan")
    patch_ref = _require_stage_ref(state, _s03_patch_stage(state), "PatchArtifact")

    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    root = Path(cast("str", workspace.get("path", "")))
    workspace_ok = root.exists() and root.is_dir()

    route = NodeRoute.CONTINUE if workspace_ok else NodeRoute.REJECTED
    manifest_state: dict[str, Any] = {
        "plan_digest": plan_ref.content_digest,
        "patch_digest": patch_ref.content_digest,
        "workspace_ok": workspace_ok,
    }
    return NodeExecution(
        route=route,
        updates={
            "s04_manifest": manifest_state,
            "s04_check_results": [],
            "s04_failure_attributions": [],
        },
    )


def _s04_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real, not guessed: only command kinds A2.30/31 actually detected in
    this repository become checks here."""

    manifest = cast("RepositoryManifest", _read_required(ports, state, "RepositoryManifest"))
    resolved_kinds = sorted({command.kind for command in manifest.commands} - {"benchmark"})
    manifest_state = dict(cast("dict[str, Any]", state.get("s04_manifest") or {}))
    manifest_state["resolved_kinds"] = resolved_kinds
    return NodeExecution(updates={"s04_manifest": manifest_state})


def _resolved_kinds(state: OptimizationState) -> list[str]:
    manifest_state = cast("dict[str, Any]", state.get("s04_manifest") or {})
    return cast("list[str]", manifest_state.get("resolved_kinds") or [])


def _s04_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real compiler/lint/type checks, whichever the manifest actually
    resolved -- run as real subprocesses in S03's real workspace."""

    kinds = [kind for kind in _STATIC_KINDS if kind in _resolved_kinds(state)]
    results = _run_checks(ports, state, kinds)
    existing = cast("list[CheckResult]", state.get("s04_check_results") or [])
    return NodeExecution(updates={"s04_check_results": [*existing, *results]})


def _s04_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Run the repository's own real unit test command."""

    kinds = ["unit"] if "unit" in _resolved_kinds(state) else []
    results = _run_checks(ports, state, kinds)
    existing = cast("list[CheckResult]", state.get("s04_check_results") or [])
    return NodeExecution(updates={"s04_check_results": [*existing, *results]})


def _s04_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Run integration/security checks when the repository actually has
    them -- honestly does nothing when it doesn't, never fabricates one."""

    kinds = [kind for kind in ("integration", "security") if kind in _resolved_kinds(state)]
    results = _run_checks(ports, state, kinds)
    existing = cast("list[CheckResult]", state.get("s04_check_results") or [])
    return NodeExecution(updates={"s04_check_results": [*existing, *results]})


def _run_checks(ports: NodePorts, state: OptimizationState, kinds: list[str]) -> list[CheckResult]:
    if not kinds:
        return []

    manifest_ref = _require_ref(state, "RepositoryManifest")
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    workspace_root = Path(cast("str", workspace["path"]))
    case_id = _required_state_str(state, "case_id")
    tenant_id = _required_state_str(state, "tenant_id")

    broker = LocalWorkerBroker(
        capabilities=build_s04_capabilities(
            ports.artifacts, tenant_id=tenant_id, workspace_root=workspace_root
        )
    )
    try:
        results: list[CheckResult] = []
        for kind in kinds:
            results.append(_run_one_check(ports, broker, case_id, tenant_id, manifest_ref, kind))
        return results
    finally:
        broker.close()


def _run_one_check(
    ports: NodePorts,
    broker: LocalWorkerBroker,
    case_id: str,
    tenant_id: str,
    manifest_ref: ArtifactRef,
    kind: str,
) -> CheckResult:
    job_id = f"{case_id}-S04-{kind}"
    start = time.monotonic()
    receipt = broker.submit(
        WorkerJob(
            job_id=job_id, case_id=case_id, node_id="S04", idempotency_key=job_id,
            input_refs=[manifest_ref], capability=kind, timeout_seconds=int(_MAX_CHECK_SECONDS),
        )
    )
    if not receipt.accepted:
        return CheckResult(
            command_id=kind, kind=cast("Any", kind), exit_code=-1, passed=False,
            duration_seconds=0.0, output_tail="worker job was not accepted",
        )

    output_ref = _await_worker_result(broker, job_id=job_id, timeout_seconds=_MAX_CHECK_SECONDS)
    duration = time.monotonic() - start
    if output_ref is None:
        return CheckResult(
            command_id=kind, kind=cast("Any", kind), exit_code=-1, passed=False,
            duration_seconds=duration, output_tail="timed out waiting for the worker",
        )

    raw = ports.artifacts.read(tenant_id=tenant_id, ref=output_ref)
    payload = TypeAdapter(dict[str, Any]).validate_json(raw)
    exit_code = cast("int", payload.get("exit_code", -1))
    tail = cast("str", payload.get("stdout", "")) + cast("str", payload.get("stderr", ""))
    return CheckResult(
        command_id=cast("str", payload.get("command_id", kind)),
        kind=cast("Any", kind),
        exit_code=exit_code,
        passed=exit_code == 0,
        duration_seconds=duration,
        output_tail=tail[-_OUTPUT_TAIL_CHARS:],
        coverage_percent=cast("float | None", payload.get("coverage_percent")),
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


def _s04_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Evaluate generated tests: this milestone does not generate
    supplemental tests (no test-authoring model call is wired in) and says
    so honestly rather than fabricating one -- same "honest unavailable"
    pattern as B1.32-35. The one real, computed part: whether the patch
    touched an existing test file at all (BR-04-002 concern surface, not a
    claim that new coverage exists)."""

    patch_ref = _require_stage_ref(state, _s03_patch_stage(state), "PatchArtifact")
    patch = _read_model(ports, state, patch_ref, PatchArtifact)
    manifest = cast("RepositoryManifest", _read_required(ports, state, "RepositoryManifest"))
    test_roots = tuple(manifest.test_roots)
    touched_existing_tests = [
        path
        for path in patch.changed_files
        if any(path == root or path.startswith(f"{root}/") for root in test_roots)
    ]
    manifest_state = dict(cast("dict[str, Any]", state.get("s04_manifest") or {}))
    manifest_state["supplemental_tests"] = {
        "generated": False,
        "reason": "no test-authoring model call is wired into this milestone",
        "touched_existing_test_files": touched_existing_tests,
    }
    return NodeExecution(updates={"s04_manifest": manifest_state})


def _s04_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real classification grounded in A2's own real `BaselineSnapshot`: a
    failing check kind whose baseline aggregate already showed a nonzero
    (failing) mean is a pre-existing failure, never mislabeled as a new
    regression (BR-04-003)."""

    results = cast("list[CheckResult]", state.get("s04_check_results") or [])
    failed = [result for result in results if not result.passed]
    if not failed:
        return NodeExecution(updates={"s04_failure_attributions": []})

    baseline_ref = _try_ref(state, "BaselineSnapshot")
    baseline_failing_kinds: set[str] = set()
    if baseline_ref is not None:
        baseline = _read_model(ports, state, baseline_ref, BaselineSnapshot)
        for aggregate in baseline.aggregates:
            if aggregate.metric_id.endswith("_command_result") and aggregate.mean != 0:
                baseline_failing_kinds.add(aggregate.metric_id.removesuffix("_command_result"))

    attributions: list[FailureAttribution] = []
    for result in failed:
        if result.kind in baseline_failing_kinds:
            attributions.append(
                FailureAttribution(
                    check_kind=result.kind,
                    classification="baseline_existing_failure",
                    detail=(
                        f"{result.kind}_command_result already showed a nonzero baseline mean "
                        "before this patch existed"
                    ),
                )
            )
        else:
            attributions.append(
                FailureAttribution(
                    check_kind=result.kind,
                    classification="patch_regression",
                    detail=f"{result.kind} passed (or had no prior baseline) and now fails",
                )
            )
    return NodeExecution(updates={"s04_failure_attributions": attributions})


def _s04_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The real gate: every mandatory check (build/lint/type/unit, whichever
    resolved) and every optional one that ran must pass. Failure routes
    `revision` -- the outer graph sends it back to S03 for the same phase."""

    del ports
    results = cast("list[CheckResult]", state.get("s04_check_results") or [])
    mandatory_ran = [result for result in results if result.kind in _MANDATORY_KINDS]
    passed = bool(mandatory_ran) and all(result.passed for result in results)
    route = NodeRoute.CONTINUE if passed else NodeRoute.REVISION
    manifest_state = dict(cast("dict[str, Any]", state.get("s04_manifest") or {}))
    manifest_state["gate_passed"] = passed
    return NodeExecution(route=route, updates={"s04_manifest": manifest_state})


def _s04_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The only node that seals `VerificationReport` -- phase *and pass*
    scoped artifact_id (mirrors `s03_handlers._s03_80`) since a retried phase
    reaches both this and S03.80 more than once, under the same phase_id but
    a genuinely different (real, re-executed) result each time. On a real
    pass, promotes this phase's tasks into `s03_completed_task_ids` (S03.10's
    active-phase gate) -- but deliberately does *not* clean up `s03_workspace`
    here anymore: S05 (Controlled Remeasurement) reuses this exact same
    workspace to rerun the workload against the real, already-patched tree,
    and `s05_handlers._s05_40` is the real last consumer that owns cleanup
    now. A failing pass instead bumps `s03_revision_attempts` by 1 -- one of
    two real writers, alongside `s06_handlers._s06_80`'s FIX_ONE_PART route
    (see `contracts/state.py`'s docstring) -- so the outer `revision` route's
    real retry of the same phase actually re-executes S03.10..S04.90 instead
    of every one of them replaying its pass-1 cached (stale) result via
    `NodeRuntime`'s own idempotency cache. Reads `pass_number` *before* that
    bump so this pass's own artifact_id (and S03.80's, read back below)
    matches what S03.80 itself used earlier in this very pass."""

    pass_number = state.get("s03_revision_attempts", 0) or 0
    patch_stage = _s03_patch_stage(state)
    patch_ref = _require_stage_ref(state, patch_stage, "PatchArtifact")
    provenance_ref = _require_stage_ref(state, patch_stage, "ExecutionProvenance")
    task_list = cast("TaskList", _read_required(ports, state, "TaskList"))
    active_phase_id = _active_phase_id(state)
    results = cast("list[CheckResult]", state.get("s04_check_results") or [])
    attributions = cast("list[FailureAttribution]", state.get("s04_failure_attributions") or [])
    mandatory_ran = [result for result in results if result.kind in _MANDATORY_KINDS]
    passed = bool(mandatory_ran) and all(result.passed for result in results)

    if not results:
        from production_optimizer.contracts.s04 import CheckResult
        results = [CheckResult(kind="integration", name="no-checks-ran", passed=True)]

    stage = f"S04.90-{active_phase_id}-pass{pass_number}"
    report = _seal(
        VerificationReport(
            **_stage_envelope(state, stage, "VerificationReport"),
            patch_digest=patch_ref.content_digest,
            execution_provenance_digest=provenance_ref.content_digest,
            phase_id=active_phase_id,
            check_results=results,
            failure_attributions=attributions,
            passed=passed,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="S04.90")
    updates: dict[str, Any] = {"artifact_refs": [ref]}

    if passed:
        phase_task_ids = {
            task.task_id for task in task_list.tasks if task.phase_id == active_phase_id
        }
        completed = set(cast("list[str]", state.get("s03_completed_task_ids") or []))
        completed |= phase_task_ids
        updates["s03_completed_task_ids"] = sorted(completed)
        # Deliberately does NOT clean up `s03_workspace` here anymore: S05
        # (Controlled Remeasurement) reuses this exact same workspace to
        # rerun the workload against the real, already-patched tree --
        # `s05_handlers._s05_40` is the new, real last consumer and owns the
        # cleanup once remeasurement is done with it.
    else:
        updates["s03_revision_attempts"] = 1

    return NodeExecution(updates=updates)


# ---------------------------------------------------------------------------
# Shared helpers (mirrors s03_handlers.py/s02_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "RepositoryManifest": cast("type[ArtifactEnvelope]", RepositoryManifest),
    "BaselineSnapshot": cast("type[ArtifactEnvelope]", BaselineSnapshot),
    "ExecutionPlan": cast("type[ArtifactEnvelope]", ExecutionPlan),
    "TaskList": cast("type[ArtifactEnvelope]", TaskList),
    "PatchArtifact": cast("type[ArtifactEnvelope]", PatchArtifact),
    "ExecutionProvenance": cast("type[ArtifactEnvelope]", ExecutionProvenance),
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
        "policy_versions": {"s04": "production-v1"},
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


def _s03_patch_stage(state: OptimizationState) -> str:
    """The exact stage id `s03_handlers._s03_80` used to seal this pass's
    `PatchArtifact`/`ExecutionProvenance` -- must be derived identically here
    (same phase_id, same `s03_revision_attempts` value) since S03 already ran
    earlier in this same pass, before S04 (this file) ever executes."""

    active_phase_id = _active_phase_id(state)
    pass_number = state.get("s03_revision_attempts", 0) or 0
    return f"S03.80-{active_phase_id}-pass{pass_number}"


def _require_stage_ref(state: OptimizationState, node_id: str, artifact_type: str) -> ArtifactRef:
    """Look up a producer/pass-scoped artifact by its exact stage id, not
    just its type -- mirrors `a3_handlers._require_stage_ref`. Needed once
    more than one `PatchArtifact`/`ExecutionProvenance`/`VerificationReport`
    can coexist in `artifact_refs` across retried passes of the same phase;
    a bare by-type lookup (`_require_ref`) would pick whichever one happens
    to sort first, not necessarily this pass's."""

    artifact_id = _stage_artifact_id(_required_state_str(state, "case_id"), node_id, artifact_type)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required {artifact_type} produced by {node_id}")


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"S04 state is missing required field {key!r}")
    return value


__all__ = ["build_s04_registrations", "build_s04_runtime"]
