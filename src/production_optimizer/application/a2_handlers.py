from __future__ import annotations

import ast
import os
import platform
import re
import shutil
import subprocess
import time
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application.a2_worker_capabilities import BENCHMARK_JSON_FILENAME
from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
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
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelMessage,
    ModelRole,
    PolicyRequest,
    WorkerJob,
)
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

# A2.40 binds evidence requirements to a *named* collector without invoking
# anything: no live CollectorRegistration/registry port exists yet
# (contracts/registries.py defines the shape, but there is no adapter that
# resolves it at runtime). This static table is a versioned, deterministic
# stand-in scoped to source types the local pilot flow can plan for; it
# never claims a sample was actually collected.
_COLLECTOR_CATALOG_VERSION = "a2-static-collector-catalog-v1"
_COLLECTOR_CATALOG: dict[str, str] = {
    "test": "local-pytest-runner",
    "benchmark": "local-benchmark-runner",
    "telemetry": "otel-local-collector",
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
A2_BLOCKED_NODES: dict[str, str] = {}


def build_a2_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build a runtime with all 18 production A2 handlers registered.

    A2.50/A2.60/A2.61/A2.62 require `ports.policy`/`ports.workers` to be
    non-`None` (they raise `RuntimeError` at call time otherwise); use
    `a2_worker_capabilities.build_local_command_capabilities` to give a
    `LocalWorkerBroker` real "unit"/"lint"/"type"/"benchmark" capabilities.
    A2.31's `ports.model` is optional -- only reached (and only then
    required) when neither a pyproject.toml convention nor a CI config step
    resolved any command at all; when absent it just records that gap in
    `VerificationManifest.rejected_commands` instead of raising. The rest
    only need `artifacts`/`intents`. A2.95 additionally raises `ValueError`
    if A2.90's quality gate or A2.91's comparability verdict did not pass —
    baseline publication is fail-closed, not best-effort.
    """

    return NodeRuntime(build_a2_registrations(), ports=ports)


def build_a2_registrations() -> dict[str, RegisteredNode]:
    return build_bound_a2_registrations()


# Must match the conditional edges `orchestration/subgraphs/a2.py` compiles
# for these two nodes. A2.90/A2.91 are the only A2 nodes the graph can route
# away from A2.95 on, so their handlers must be able to emit those routes —
# see `_a2_90`/`_a2_91`.
_A2_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "A2.31": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value},
    "A2.90": {NodeRoute.CONTINUE.value, NodeRoute.MISSING.value, NodeRoute.REJECTED.value},
    "A2.91": {NodeRoute.CONTINUE.value, NodeRoute.MISSING.value, NodeRoute.INCOMPARABLE.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _A2_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    side_effect_class = (
        SideEffectClass.EXTERNAL_JOB
        if node_id in {"A2.31", "A2.50", "A2.60", "A2.61", "A2.62"}
        else SideEffectClass.IDEMPOTENT_WRITE
    )
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-1-a2",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="a2-production-v1",
        side_effect_class=side_effect_class,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/implementation/05-lane-1-detailed-implementation-playbook.md",
        slo="A2 node completes within 60 seconds for local repositories",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production A2 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"a2_{node_id.replace('.', '_')}"
    return execute


def build_bound_a2_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import A2_NODE_IDS

    enabled = {
        "A2.10",
        "A2.20",
        "A2.30",
        "A2.31",
        "A2.40",
        "A2.41",
        "A2.50",
        "A2.60",
        "A2.61",
        "A2.62",
        "A2.63",
        "A2.64",
        "A2.70",
        "A2.71",
        "A2.80",
        "A2.90",
        "A2.91",
        "A2.95",
    }
    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in A2_NODE_IDS
        if node_id in enabled
    }


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "A2.10":
            return _a2_10(state, ports)
        case "A2.20":
            return _a2_20(state, ports)
        case "A2.30":
            return _a2_30(state, ports)
        case "A2.31":
            return _a2_31(state, ports)
        case "A2.40":
            return _a2_40(state, ports)
        case "A2.41":
            return _a2_41(state, ports)
        case "A2.50":
            return _a2_50(state, ports)
        case "A2.60":
            return _a2_60(state, ports)
        case "A2.61":
            return _a2_61(state, ports)
        case "A2.62":
            return _a2_62(state, ports)
        case "A2.63":
            return _a2_63(state, ports)
        case "A2.64":
            return _a2_64(state, ports)
        case "A2.70":
            return _a2_70(state, ports)
        case "A2.71":
            return _a2_71(state, ports)
        case "A2.80":
            return _a2_80(state, ports)
        case "A2.90":
            return _a2_90(state, ports)
        case "A2.91":
            return _a2_91(state, ports)
        case "A2.95":
            return _a2_95(state, ports)
        case _:
            return NodeExecution()


def _a2_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)

    mismatches: list[str] = []
    source_reachable = False

    if not request.approval:
        mismatches.append("approval not present")

    if request.approval and request.approval.artifact_digest != request.request_fingerprint:
        mismatches.append("approval digest mismatch")

    try:
        canonical_path = Path(request.source.allowed_root_id) / request.source.relative_path
        if canonical_path.exists():
            source_reachable = True
    except (OSError, ValueError):
        mismatches.append("source path unreachable")

    decision = _seal(
        A2IntakeDecision(
            **_base_envelope(state, "A2IntakeDecision", parents=[request.content_digest]),
            verified=not mismatches and source_reachable,
            mismatches=mismatches,
            source_reachable=source_reachable,
            policy_version=request.approval.policy_version if request.approval else "unknown",
        )
    )
    ref = _put_envelope(ports, state, decision, node_id="A2.10")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)

    canonical_path = Path(request.source.allowed_root_id) / request.source.relative_path
    if not canonical_path.exists():
        raise ValueError(f"source path does not exist: {canonical_path}")

    git_revision = _git(canonical_path, "rev-parse", "HEAD")
    status = _git(canonical_path, "status", "--porcelain=v1")
    dirty_lines = [line for line in status.splitlines() if line.strip()]
    dirty = bool(dirty_lines)

    files: list[FileIdentity] = []
    exclusions: list[str] = []

    for path in canonical_path.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(canonical_path).as_posix()
        if _is_disallowed_path(relative):
            exclusions.append(relative)
            continue

        content = path.read_bytes()
        file_digest = sha256_digest(content)
        executable = path.stat().st_mode & 0o111 != 0

        files.append(
            FileIdentity(
                relative_path=relative,
                content_digest=file_digest,
                executable=executable,
            )
        )

    submodule_revisions: dict[str, str] = {}
    submodule_status = _git(canonical_path, "submodule", "status")
    for line in submodule_status.splitlines():
        if line.strip():
            parts = line.split()
            if len(parts) >= 2:
                revision = parts[0].lstrip("+-U ")
                path_name = parts[1]
                submodule_revisions[path_name] = revision

    snapshot = _seal(
        SourceSnapshot(
            **_base_envelope(state, "SourceSnapshot", parents=[request.content_digest]),
            repository_id=_repository_id(canonical_path),
            canonical_path_ref=str(canonical_path),
            git_revision=git_revision or None,
            dirty=dirty,
            files=sorted(files, key=lambda f: f.relative_path),
            submodule_revisions=submodule_revisions,
            exclusions=sorted(exclusions),
        )
    )
    ref = _put_envelope(ports, state, snapshot, node_id="A2.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    root = Path(snapshot.canonical_path_ref)

    suffix_counts: dict[str, int] = {}
    manifest_files: list[str] = []
    test_roots: set[str] = set()
    modules: set[str] = set()

    for file_identity in snapshot.files:
        relative = file_identity.relative_path
        path = Path(relative)
        suffix = path.suffix.lower()
        if suffix:
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        if path.name in _MANIFEST_FILE_NAMES:
            manifest_files.append(relative)
        # `suffix == ".py"` matters: a directory literally named test/tests
        # is not evidence of a *Python* test suite on its own -- a Node/Go/
        # Rust repo's `test/` full of non-Python files would otherwise mark
        # tool_coverage["pytest"] = 1.0, sending `python -m pytest` at a
        # directory with nothing for it to collect (a real, misleading
        # "unit_command_result" exit code, not an honest "unavailable").
        if suffix == ".py" and path.parent.name in {"test", "tests", "__tests__"}:
            test_roots.add(path.parent.as_posix())
        if len(path.parts) > 1:
            modules.add(path.parts[0])

    total = sum(suffix_counts.values()) or 1
    languages = {
        _LANGUAGE_BY_SUFFIX[suffix]: count / total
        for suffix, count in suffix_counts.items()
        if suffix in _LANGUAGE_BY_SUFFIX
    }

    pyproject_config = _read_pyproject(root)
    tool_coverage = {
        "pytest": 1.0 if test_roots or "tool.pytest.ini_options" in pyproject_config else 0.0,
        "ruff": 1.0 if "tool.ruff" in pyproject_config else 0.0,
        "mypy": 1.0 if "tool.mypy" in pyproject_config else 0.0,
        "pytest_benchmark": 1.0 if _has_pytest_benchmark_dependency(pyproject_config) else 0.0,
    }

    manifest = _seal(
        RepositoryManifest(
            **_base_envelope(state, "RepositoryManifest", parents=[snapshot.content_digest]),
            languages=languages,
            modules=sorted(modules),
            manifest_files=sorted(manifest_files),
            test_roots=sorted(test_roots),
            commands=[],
            tool_coverage=tool_coverage,
            exclusions=list(snapshot.exclusions),
        )
    )
    ref = _put_envelope(ports, state, manifest, node_id="A2.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_31(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    manifest = _read_model(
        ports, state, _require_ref(state, "RepositoryManifest"), RepositoryManifest
    )
    root = Path(snapshot.canonical_path_ref)

    commands: list[RepositoryCommand] = []
    rejected: list[str] = []

    if manifest.tool_coverage.get("pytest", 0.0) > 0:
        commands.append(
            RepositoryCommand(
                command_id="unit-tests",
                argv=["python", "-m", "pytest"],
                working_directory=str(root),
                kind="unit",
                source="pyproject_toml",
            )
        )
    else:
        rejected.append("unit: no pytest configuration or test root detected")

    if manifest.tool_coverage.get("ruff", 0.0) > 0:
        commands.append(
            RepositoryCommand(
                command_id="lint",
                argv=["python", "-m", "ruff", "check", "."],
                working_directory=str(root),
                kind="lint",
                source="pyproject_toml",
            )
        )
    else:
        rejected.append("lint: no [tool.ruff] configuration detected")

    if manifest.tool_coverage.get("mypy", 0.0) > 0:
        commands.append(
            RepositoryCommand(
                command_id="type-check",
                argv=["python", "-m", "mypy", "."],
                working_directory=str(root),
                kind="type",
                source="pyproject_toml",
            )
        )
    else:
        rejected.append("type: no [tool.mypy] configuration detected")

    if manifest.tool_coverage.get("pytest_benchmark", 0.0) > 0:
        commands.append(
            RepositoryCommand(
                command_id="benchmark",
                argv=[
                    "python",
                    "-m",
                    "pytest",
                    "--benchmark-only",
                    f"--benchmark-json={BENCHMARK_JSON_FILENAME}",
                ],
                working_directory=str(root),
                kind="benchmark",
                source="pyproject_toml",
            )
        )
    else:
        rejected.append("benchmark: no pytest-benchmark dependency detected")

    # CI config is a higher-trust signal than pyproject.toml section presence
    # (it's the exact command the project's own CI already runs) -- it only
    # fills gaps the pyproject.toml heuristic above left rejected, never
    # overrides a kind that heuristic already resolved.
    resolved_kinds = {command.kind for command in commands}
    # `_detect_ci_commands` only ever proposes a "unit" command today, so
    # skip it entirely once that kind is already resolved -- calling it
    # anyway would still be correct (it fills gaps, never overrides) but
    # means an unconditional `act -l` subprocess (a real Docker call, tens
    # of seconds worst case) on every single A2.31 run, for a result that
    # gets thrown away.
    if "unit" not in resolved_kinds:
        for kind, ci_command in _detect_ci_commands(root).items():
            if kind in resolved_kinds:
                continue
            commands.append(ci_command)
            resolved_kinds.add(kind)
            rejected = [reason for reason in rejected if not reason.startswith(f"{kind}:")]

    # Last resort: nothing real was detected at all (no known pyproject.toml
    # convention, no matching CI step) -- ask the model what it would run,
    # but never execute an LLM guess without a human approving it first
    # (unlike every command above, which are real, detected-not-guessed).
    if not commands:
        resume_command = state.get("resume_command")
        if resume_command is not None:
            proposal = state.get("pending_command_proposal")
            if resume_command.decision == "approve" and proposal is not None:
                commands = [RepositoryCommand.model_validate(proposal)]
            else:
                rejected.append(
                    f"llm_suggested: resumed with decision {resume_command.decision!r}"
                )
        elif ports.model is not None:
            proposal = _propose_command_via_llm(ports, state, snapshot, manifest)
            if proposal is None:
                rejected.append("llm_suggested: model did not return a usable proposal")
            else:
                return _halt_for_command_approval(state, ports, proposal)
        else:
            rejected.append("llm_suggested: no ModelProviderPort wired into A2")

    verification = _seal(
        VerificationManifest(
            **_base_envelope(
                state,
                "VerificationManifest",
                parents=[snapshot.content_digest, manifest.content_digest],
            ),
            commands=commands,
            rejected_commands=rejected,
        )
    )
    ref = _put_envelope(ports, state, verification, node_id="A2.31")
    return NodeExecution(updates={"artifact_refs": [ref]})


# `act` (nektos/act) replays a `.github/workflows/*.yml` job in a real
# Docker container -- the project's own CI job, not a line of `run:` text
# parsed with a keyword guess. `catthehacker/ubuntu:act-latest` (act's own
# default `ubuntu-latest` mapping) is multiple GB; a small, already-common
# image keeps first-run latency inside a worker job's usual timeout budget.
# It only needs a POSIX shell + coreutils to execute typical `run:` steps.
_ACT_RUNNER_IMAGE = "node:20-bullseye-slim"
_ACT_LIST_TIMEOUT_SECONDS = 30


def _detect_ci_commands(root: Path) -> dict[str, RepositoryCommand]:
    """The repo's own GitHub Actions CI job, run for real via `act`.

    Higher trust than the pyproject.toml heuristic below: rather than
    guessing which `run:` line is "the test command" (a previous version of
    this function did exactly that with a keyword regex), this replays the
    first real job `act` finds -- exactly the container/steps the project's
    own CI already runs, not an inference. Requires `act` on PATH and a
    reachable Docker daemon; either being unavailable is a real, honestly
    reported gap (falls through to the next tier), not a crash. Only
    `.github/workflows/*.y*ml` is supported today -- GitLab CI/Makefile are
    a documented future extension.
    """

    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir() or shutil.which("act") is None:
        return {}

    try:
        result = subprocess.run(
            ["act", "-l", "-P", f"ubuntu-latest={_ACT_RUNNER_IMAGE}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_ACT_LIST_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}

    job_id = _first_act_job_id(result.stdout)
    if job_id is None:
        return {}

    return {
        "unit": RepositoryCommand(
            command_id=f"ci-act-{job_id}",
            argv=["act", "-j", job_id, "-P", f"ubuntu-latest={_ACT_RUNNER_IMAGE}"],
            working_directory=str(root),
            kind="unit",
            source="ci_config",
        )
    }


def _first_act_job_id(act_list_output: str) -> str | None:
    """Parse `act -l`'s table for the first real Job ID.

    `act -l` prints a leading `level=info` log line, then a header
    (`Stage  Job ID  Job name  ...`), then one row per job. Job IDs are
    GitHub Actions identifiers (`[_a-zA-Z][a-zA-Z0-9_-]*`, never containing
    whitespace per the workflow syntax spec), so splitting each data row on
    runs of 2+ spaces reliably isolates it even when a Job *name* column
    contains a single space -- unlike a naive `.split()`.
    """

    lines = [line for line in act_list_output.splitlines() if line.strip()]
    header_index = next((i for i, line in enumerate(lines) if line.startswith("Stage")), None)
    if header_index is None:
        return None
    for line in lines[header_index + 1 :]:
        columns = re.split(r" {2,}", line.strip())
        if len(columns) >= 2 and columns[1]:
            return columns[1]
    return None


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


def _a2_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)

    bindings: list[CollectorBinding] = []
    unresolved: list[str] = []

    for requirement in request.evidence_requirements:
        collector_id = _resolve_collector(requirement)
        if collector_id is None:
            unresolved.append(requirement.requirement_id)
            continue
        bindings.append(
            CollectorBinding(
                requirement_id=requirement.requirement_id,
                collector_id=collector_id,
                source_type=_matched_source_type(requirement),
                window_seconds=3600,
                aggregation="mean",
                minimum_samples=requirement.minimum_samples,
            )
        )

    if unresolved:
        raise ValueError(
            f"A2.40 cannot bind mandatory evidence requirements: {sorted(unresolved)}"
        )

    plan = _seal(
        CollectorPlan(
            **_base_envelope(state, "CollectorPlan", parents=[request.content_digest]),
            bindings=bindings,
            unresolved_requirements=unresolved,
            catalog_version=_COLLECTOR_CATALOG_VERSION,
        )
    )
    ref = _put_envelope(ports, state, plan, node_id="A2.40")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_41(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)

    tool_versions = {
        "python": platform.python_version(),
        "git": _git(Path.cwd(), "--version").removeprefix("git version ").strip(),
    }

    manifest = _seal(
        EnvironmentManifest(
            **_base_envelope(state, "EnvironmentManifest", parents=[request.content_digest]),
            tool_versions={k: v for k, v in tool_versions.items() if v},
            hardware_profile=platform.platform(),
            concurrency=max(os.cpu_count() or 1, 1),
            cache_state=request.workload.cache_state,
        )
    )
    ref = _put_envelope(ports, state, manifest, node_id="A2.41")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Authorize repository-owned commands and submit worker jobs for them.

    Scope is deliberately narrow: only `VerificationManifest.commands` (real,
    tool-detected argv from A2.31) are authorized and dispatched through
    `ports.workers`. `CollectorPlan.bindings` (A2.40) are policy-checked too
    so a denial is visible, but no job is submitted for them — there is no
    analyzer/collector adapter yet (A2.60-A2.64 stay blocked) to consume such
    a job, and submitting one anyway would fabricate work that never runs.
    """

    if ports.policy is None or ports.workers is None:
        raise RuntimeError("A2.50 requires PolicyPort and WorkerBroker wired into NodePorts")

    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    verification = _read_model(
        ports, state, _require_ref(state, "VerificationManifest"), VerificationManifest
    )
    plan = _read_model(ports, state, _require_ref(state, "CollectorPlan"), CollectorPlan)
    env = _read_model(ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest)

    tenant_id = _required_state_str(state, "tenant_id")
    case_id = _required_state_str(state, "case_id")
    policy_version = request.approval.policy_version if request.approval else "unknown"

    denied: list[str] = []
    worker_job_ids: list[str] = []

    snapshot_ref = _require_ref(state, "SourceSnapshot")
    verification_ref = _require_ref(state, "VerificationManifest")
    for command in verification.commands:
        decision = ports.policy.evaluate(
            PolicyRequest(
                decision_type="a2.execution_authorization",
                policy_version=policy_version,
                tenant_id=tenant_id,
                facts={
                    "argv": command.argv,
                    "working_directory": command.working_directory,
                    "kind": command.kind,
                    "timeout_seconds": request.budget.maximum_worker_seconds,
                },
            )
        )
        if not decision.allowed:
            denied.append(f"{command.command_id}: {decision.decision}")
            continue

        job_id = _worker_job_id(case_id, command.command_id)
        receipt = ports.workers.submit(
            WorkerJob(
                job_id=job_id,
                case_id=case_id,
                node_id="A2.50",
                idempotency_key=job_id,
                input_refs=[snapshot_ref, verification_ref],
                capability=command.kind,
                timeout_seconds=request.budget.maximum_worker_seconds,
                secret_refs=[],
            )
        )
        if receipt.accepted:
            worker_job_ids.append(job_id)
        else:
            denied.append(f"{command.command_id}: worker broker rejected submission")

    for binding in plan.bindings:
        decision = ports.policy.evaluate(
            PolicyRequest(
                decision_type="a2.collector_authorization",
                policy_version=policy_version,
                tenant_id=tenant_id,
                facts={
                    "collector_id": binding.collector_id,
                    "source_type": binding.source_type,
                },
            )
        )
        if not decision.allowed:
            denied.append(f"{binding.requirement_id}: {decision.decision}")

    authorization = _seal(
        ExecutionAuthorization(
            **_base_envelope(
                state,
                "ExecutionAuthorization",
                parents=[
                    snapshot.content_digest,
                    verification.content_digest,
                    plan.content_digest,
                    env.content_digest,
                ],
            ),
            authorized=not denied,
            denied_capabilities=denied,
            allowed_write_roots=sorted({c.working_directory for c in verification.commands}),
            allowed_egress=[],
            worker_job_ids=worker_job_ids,
            policy_version=policy_version,
        )
    )
    ref = _put_envelope(ports, state, authorization, node_id="A2.50")
    return NodeExecution(updates={"artifact_refs": [ref]})


_WORKER_POLL_INTERVAL_SECONDS = 0.05
_WORKER_POLL_MAX_SECONDS = 30.0


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
) -> EvidenceIdentity:
    return EvidenceIdentity(
        feature_id=request.objective.feature_id,
        repository_id=snapshot.repository_id,
        source_snapshot_digest=snapshot.content_digest,
        workload_id=request.workload.workload_id,
        environment_id=request.workload.environment_id,
        hardware_profile=env.hardware_profile,
        concurrency=env.concurrency,
        cache_state=env.cache_state,
        metric_schema_version="1.0",
        sample_id=sample_id,
        observed_at=datetime.now(UTC),
        collector=collector,
        collector_version=collector_version,
    )


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
                identity = _evidence_identity(
                    request,
                    snapshot,
                    env,
                    sample_id=f"{command.command_id}-{round_index}",
                    collector="a2-local-worker-broker",
                    collector_version="1.0.0",
                )
                evidence.append(
                    EvidenceItem(
                        evidence_id=f"{case_id}:{node_id}:{command.command_id}-{round_index}",
                        evidence_type=f"{kind}_command_result",
                        trust_level=TrustLevel.T2,
                        identity=identity,
                        raw_ref=raw_ref,
                        value=float(cast("float", round_data["value"])),
                        unit=cast("str", round_data.get("unit", "seconds")),
                    )
                )
            coverage[kind] = 1.0
            continue

        exit_code = payload.get("exit_code")

        identity = _evidence_identity(
            request,
            snapshot,
            env,
            sample_id=f"{command.command_id}-1",
            collector="a2-local-worker-broker",
            collector_version="1.0.0",
        )
        evidence.append(
            EvidenceItem(
                evidence_id=f"{case_id}:{node_id}:{command.command_id}",
                evidence_type=f"{kind}_command_result",
                trust_level=TrustLevel.T2,
                identity=identity,
                raw_ref=raw_ref,
                value=float(exit_code) if isinstance(exit_code, int | float) else None,
                unit="exit_code",
            )
        )
        coverage[kind] = 1.0

    branch = _seal(
        BranchEvidenceRefs(
            **_branch_envelope(
                state,
                node_id,
                parents=[
                    snapshot.content_digest,
                    verification.content_digest,
                    authorization.content_digest,
                ],
            ),
            branch_id=node_id,
            branch_kind=branch_kind,
            evidence=evidence,
            coverage=coverage,
            unavailable_reason="; ".join(unavailable) if unavailable else None,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id=node_id)
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    return _run_command_evidence_branch(
        state, ports, node_id="A2.60", branch_kind="static", command_kinds=("lint", "type")
    )


def _a2_61(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    return _run_command_evidence_branch(
        state, ports, node_id="A2.61", branch_kind="test", command_kinds=("unit",)
    )


def _a2_62(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    return _run_command_evidence_branch(
        state, ports, node_id="A2.62", branch_kind="metric", command_kinds=("benchmark",)
    )


def _a2_63(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Always-unavailable telemetry branch: no telemetry query adapter exists.

    `CollectorPlan.bindings` (A2.40) can name a telemetry collector (see
    `_COLLECTOR_CATALOG`), but nothing in `NodePorts` can query it — there is
    no Prometheus/Loki/Tempo/OTel query client wired up. Recording that gap
    honestly (rather than staying unregistered) matches the platform rule
    that zero sources is a legitimate, visible outcome, not a missing node.
    """

    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    plan = _read_model(ports, state, _require_ref(state, "CollectorPlan"), CollectorPlan)

    telemetry_bindings = [b for b in plan.bindings if b.source_type == "telemetry"]
    if telemetry_bindings:
        reason = (
            f"no telemetry query adapter is wired into NodePorts; "
            f"{len(telemetry_bindings)} evidence requirement(s) bound to a telemetry "
            f"collector in A2.40 remain unexecuted"
        )
    else:
        reason = "no evidence requirement in this request was bound to a telemetry collector"

    branch = _seal(
        BranchEvidenceRefs(
            **_branch_envelope(
                state, "A2.63", parents=[snapshot.content_digest, plan.content_digest]
            ),
            branch_id="A2.63",
            branch_kind="telemetry",
            evidence=[],
            coverage={},
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A2.63")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_64(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Build a file/import/function map by parsing Python source with `ast`.

    Entirely local and deterministic — no analyzer registry or worker job is
    needed for this language. Non-Python files and files that fail to parse
    are recorded as unresolved gaps, never silently dropped, per the
    playbook's "dynamic gaps remain unknown; never filled by narrative" rule.
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    env = _read_model(ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest)
    root = Path(snapshot.canonical_path_ref)

    nodes: dict[str, dict[str, list[str]]] = {}
    unresolved: list[str] = []

    for file_identity in snapshot.files:
        relative = file_identity.relative_path
        if not relative.endswith(".py"):
            unresolved.append(f"{relative}: unsupported language")
            continue

        try:
            source = (root / relative).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            unresolved.append(f"{relative}: {exc.__class__.__name__}")
            continue

        imports: set[str] = set()
        functions: list[str] = []
        for stmt in ast.walk(tree):
            if isinstance(stmt, ast.Import):
                imports.update(alias.name for alias in stmt.names)
            elif isinstance(stmt, ast.ImportFrom) and stmt.module:
                imports.add(stmt.module)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append(stmt.name)

        nodes[relative] = {"imports": sorted(imports), "functions": sorted(functions)}

    graph_content = canonical_json({"nodes": nodes, "unresolved": sorted(unresolved)})
    graph_ref = ports.artifacts.put_blob(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=graph_content,
        content_digest=sha256_digest(graph_content),
        media_type="application/json",
    )

    evidence: list[EvidenceItem] = []
    total_files = len(snapshot.files) or 1
    coverage = {"resolved": len(nodes) / total_files}

    if nodes:
        identity = _evidence_identity(
            request,
            snapshot,
            env,
            sample_id="source-map-1",
            collector="a2-ast-source-mapper",
            collector_version="1.0.0",
        )
        evidence.append(
            EvidenceItem(
                evidence_id=f"{_required_state_str(state, 'case_id')}:A2.64:source-map",
                evidence_type="source_map",
                trust_level=TrustLevel.T2,
                identity=identity,
                raw_ref=graph_ref,
                value=len(nodes),
                unit="files_mapped",
            )
        )

    branch = _seal(
        BranchEvidenceRefs(
            **_branch_envelope(state, "A2.64", parents=[snapshot.content_digest]),
            branch_id="A2.64",
            branch_kind="source_map",
            evidence=evidence,
            coverage=coverage,
            unavailable_reason=None if nodes else "no supported-language files found to map",
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A2.64")
    return NodeExecution(updates={"artifact_refs": [ref]})


_FAN_IN_BRANCH_IDS = ("A2.60", "A2.61", "A2.62", "A2.63", "A2.64")


def _a2_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    branch_refs = [_require_branch_ref(state, node_id) for node_id in _FAN_IN_BRANCH_IDS]
    branches = [_read_model(ports, state, ref, BranchEvidenceRefs) for ref in branch_refs]

    branch_status = {
        branch.branch_id: ("unavailable" if branch.unavailable_reason else "collected")
        for branch in branches
    }
    evidence_ids = sorted(
        item.evidence_id for branch in branches for item in branch.evidence
    )

    fan_in = _seal(
        RawEvidenceFanIn(
            **_base_envelope(
                state, "RawEvidenceFanIn", parents=[branch.content_digest for branch in branches]
            ),
            branch_status=branch_status,
            evidence_ids=evidence_ids,
        )
    )
    ref = _put_envelope(ports, state, fan_in, node_id="A2.70")
    return NodeExecution(updates={"artifact_refs": [ref]})


# Maps `EvidenceItem.evidence_type` (as emitted by A2.60/A2.61/A2.64) to the
# `source_type` vocabulary `EvidenceRequirement.accepted_source_types` uses
# (a1.py). A2.62/A2.63 never populate evidence today (see A2_BLOCKED_NODES
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

_NORMALIZED_UNITS = frozenset({"exit_code", "files_mapped", "seconds"})


def _a2_71(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Normalize the evidence A2.70 fanned in.

    Every unit A2.60/A2.61/A2.64 currently emit (`exit_code`, `files_mapped`)
    is already in canonical form — there is no ms->s style conversion to do
    yet — so normalization here means: verify every fanned-in evidence_id
    still resolves to a real item in its owning branch, and reject (not
    silently pass through) any unit this node does not recognize. An
    evidence_id present in `RawEvidenceFanIn` but missing from its branch, or
    carrying an unrecognized unit, is a `conversion_failures` entry, never a
    fabricated pass-through.
    """

    fan_in = _read_model(ports, state, _require_ref(state, "RawEvidenceFanIn"), RawEvidenceFanIn)
    branches = [
        _read_model(ports, state, _require_branch_ref(state, node_id), BranchEvidenceRefs)
        for node_id in _FAN_IN_BRANCH_IDS
    ]
    by_id = {item.evidence_id: item for branch in branches for item in branch.evidence}

    normalized: list[EvidenceItem] = []
    failures: list[str] = []
    for evidence_id in fan_in.evidence_ids:
        item = by_id.get(evidence_id)
        if item is None:
            failures.append(f"{evidence_id}: missing from branch evidence (fan-in integrity gap)")
            continue
        if item.unit not in _NORMALIZED_UNITS:
            failures.append(f"{evidence_id}: unrecognized unit {item.unit!r}, cannot normalize")
            continue
        normalized.append(item.model_copy(update={"normalized_ref": item.raw_ref}))

    normalized_set = _seal(
        NormalizedEvidenceSet(
            **_base_envelope(state, "NormalizedEvidenceSet", parents=[fan_in.content_digest]),
            evidence=normalized,
            conversion_failures=failures,
        )
    )
    ref = _put_envelope(ports, state, normalized_set, node_id="A2.71")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a2_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Bind provenance into one `EvidenceBundle` for downstream A3 gates.

    `baseline_digest` is set to the `SourceSnapshot` digest: in this pilot's
    `active_collection` mode the snapshot *is* the baseline being measured
    (the same digest `BaselineSnapshot.source_snapshot_digest` uses at
    A2.95). No separate "baseline" artifact exists yet at this point in the
    graph to reference instead.
    """

    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    normalized = _read_model(
        ports, state, _require_ref(state, "NormalizedEvidenceSet"), NormalizedEvidenceSet
    )

    if not normalized.evidence:
        raise ValueError("A2.80 cannot bind an EvidenceBundle with zero normalized evidence items")

    collector_versions = {
        item.identity.collector: item.identity.collector_version for item in normalized.evidence
    }
    coverage: dict[str, float] = {}
    for item in normalized.evidence:
        source_type = _EVIDENCE_TYPE_SOURCE_TYPE.get(item.evidence_type, "unknown")
        coverage[source_type] = coverage.get(source_type, 0.0) + 1.0

    bundle = _seal(
        EvidenceBundle(
            **_base_envelope(
                state,
                "EvidenceBundle",
                parents=[snapshot.content_digest, normalized.content_digest],
            ),
            baseline_digest=snapshot.content_digest,
            evidence=normalized.evidence,
            collector_versions=collector_versions,
            coverage=coverage,
        )
    )
    ref = _put_envelope(ports, state, bundle, node_id="A2.80")
    return NodeExecution(updates={"artifact_refs": [ref]})


_REDACTION_KEYWORDS = ("password", "secret", "api_key", "apikey", "token=")


def _a2_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
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
    for requirement in request.evidence_requirements:
        accepted = requirement.accepted_source_types
        matching = [
            item
            for item in bundle.evidence
            if _EVIDENCE_TYPE_SOURCE_TYPE.get(item.evidence_type) in accepted
        ]
        satisfied = len(matching) >= requirement.minimum_samples
        if requirement.mandatory:
            mandatory_coverage[requirement.requirement_id] = satisfied
        if not satisfied:
            sample_failures.append(
                f"{requirement.requirement_id}: collected {len(matching)}, "
                f"needs {requirement.minimum_samples}"
            )

    now = datetime.now(UTC)
    freshness_failures = [
        f"{item.evidence_id}: observed {item.identity.observed_at.isoformat()}"
        for item in bundle.evidence
        if (now - item.identity.observed_at).total_seconds() > request.budget.deadline_seconds
    ]

    integrity_failures = [
        item.evidence_id
        for item in bundle.evidence
        if not ports.artifacts.verify(tenant_id=tenant_id, ref=item.raw_ref)
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
            freshness_failures=freshness_failures,
            integrity_failures=integrity_failures,
            redaction_failures=redaction_failures,
            collector_failures=collector_failures,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="A2.90")
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


def _a2_91(state: OptimizationState, ports: NodePorts) -> NodeExecution:
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


def _a2_95(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Aggregate eligible evidence into the sealed A3 handoff.

    Fail-closed gate per the playbook ("Complete only when quality and
    comparability are true"): refuses to publish unless both A2.90's
    `passed` and A2.91's `comparable` are true, rather than publishing a
    baseline the platform already knows is unreliable. The compiled graph
    (`orchestration/subgraphs/a2.py`) already routes away from this node to
    `END` on a failing A2.90/A2.91 route, so this `raise` is defense-in-depth
    for direct `NodeRuntime.execute("A2.95", ...)` calls that bypass the
    graph's conditional edges (as most of this file's tests do).
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    quality = _read_model(
        ports, state, _require_ref(state, "EvidenceQualityReport"), EvidenceQualityReport
    )
    comparability = _read_model(
        ports, state, _require_ref(state, "ComparabilityReport"), ComparabilityReport
    )

    if not quality.passed:
        raise ValueError("A2.95 cannot publish a baseline: EvidenceQualityReport did not pass")
    if not comparability.comparable:
        raise ValueError("A2.95 cannot publish a baseline: ComparabilityReport is not comparable")

    grouped: dict[str, list[EvidenceItem]] = {}
    for item in bundle.evidence:
        if isinstance(item.value, int | float):
            grouped.setdefault(item.evidence_type, []).append(item)

    aggregates: list[MetricAggregate] = []
    for metric_id, items in sorted(grouped.items()):
        values = sorted(float(cast("float", item.value)) for item in items)
        rank_95 = min(int(len(values) * 0.95), len(values) - 1)
        aggregates.append(
            MetricAggregate(
                metric_id=metric_id,
                unit=items[0].unit or "unitless",
                sample_ids=[item.identity.sample_id for item in items],
                count=len(values),
                minimum=values[0],
                maximum=values[-1],
                mean=sum(values) / len(values),
                percentile_50=values[len(values) // 2],
                percentile_95=values[rank_95],
            )
        )

    observed_times = [item.identity.observed_at for item in bundle.evidence]
    window_start = min(observed_times)
    window_end = max(observed_times)
    if window_end <= window_start:
        window_end = window_start + timedelta(microseconds=1)

    baseline = _seal(
        BaselineSnapshot(
            **_base_envelope(
                state,
                "BaselineSnapshot",
                parents=[
                    request.content_digest,
                    snapshot.content_digest,
                    bundle.content_digest,
                    quality.content_digest,
                    comparability.content_digest,
                ],
            ),
            request_digest=request.content_digest,
            source_snapshot_digest=snapshot.content_digest,
            workload_id=request.workload.workload_id,
            environment_id=request.workload.environment_id,
            window_start=window_start,
            window_end=window_end,
            aggregates=aggregates,
        )
    )
    ref = _put_envelope(ports, state, baseline, node_id="A2.95")
    return NodeExecution(updates={"artifact_refs": [ref], "baseline_ref": ref})


def _resolve_collector(requirement: EvidenceRequirement) -> str | None:
    for source_type in sorted(requirement.accepted_source_types):
        collector_id = _COLLECTOR_CATALOG.get(source_type)
        if collector_id is not None:
            return collector_id
    return None


def _matched_source_type(requirement: EvidenceRequirement) -> str:
    for source_type in sorted(requirement.accepted_source_types):
        if source_type in _COLLECTOR_CATALOG:
            return source_type
    raise ValueError(f"no matched source type for requirement {requirement.requirement_id!r}")


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


def _has_pytest_benchmark_dependency(pyproject_config: dict[str, Any]) -> bool:
    """Does this repo declare `pytest-benchmark` anywhere real dependencies live?

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
            if name.lower() == "pytest-benchmark":
                return True
    return False


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


def _repository_id(path: Path) -> str:
    return sha256_digest(str(path).encode("utf-8"))[:32]


def _is_disallowed_path(relative: str) -> bool:
    parts = set(relative.split("/"))
    return bool(parts & _DISALLOWED_PATHS)


__all__ = [
    "A2_BLOCKED_NODES",
    "build_a2_registrations",
    "build_a2_runtime",
    "build_bound_a2_registrations",
]
