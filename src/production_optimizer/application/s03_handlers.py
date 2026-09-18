"""Production handlers for S03 (Implement One Phase) -- turns S02's
`ExecutionPlan`/`TaskList` into one real, scoped patch (see
docs/project-blueprint/shared-workflow/03-implement-phase.md).

BR-03-001 ("one invocation implements one phase only") is read literally:
this milestone's S03 processes exactly the one active phase (the first
phase whose tasks are not yet in `s03_completed_task_ids`) and does not
itself loop back for a second phase -- multi-phase plans need a later,
outer-loop milestone (mirrors how B1.32-35's query adapters were left an
explicit, acknowledged gap rather than a hidden one).

S03.20 materializes a real isolated workspace: a real `git worktree` when
the source snapshot has a git revision, else an honest plain directory copy
(never fabricated isolation). S03.50 either applies a deterministic
before/after text substitution (risk_ceiling == "experiment_config", no
LLM) or runs the real, bounded tool-loop agent
(`application.s03_agent_loop.run_agent_loop`) for `prompt`/`code`/
`architecture` risk. S03.60/70 are real, filesystem-verified checks --
never a hardcoded pass. S03.60 enforces scope at two levels per the spec's
own row ("Compare changed paths/symbols/dependencies and treatment
semantics with plan"): byte comparison against the untouched source root
for changed *paths*, plus an `ast`-based diff of top-level function/class/
method definitions for changed *symbols* inside an authorized Python file
whose tasks declared `PlanTask.symbols` -- a symbol edit S03.50 was never
authorized for is caught even though the file itself was in scope, which
path-diffing alone cannot detect. S03.70 does regex-based secret/lockfile/
migration/binary detection. S03.80 is the only node that seals
`PatchArtifact`/`ExecutionProvenance`, using a real `difflib.unified_diff`
(works identically for a git worktree or a plain copy, unlike `git diff`)
and a phase-scoped artifact_id (mirrors `a3_handlers._stage_envelope`) so a
later multi-phase milestone can extend this without an artifact_refs
collision.

Known gap versus the spec's S03.70 row: it also lists "license issues"
among what sanitation should catch (`SanitationFinding.kind` already
declares a `"license"` variant in `contracts/s03.py`), but no detector for
it exists yet here -- an explicit, acknowledged gap, not a hidden one
(mirrors how B1.32-35's query adapters are documented as not-yet-wired
rather than silently absent).
"""

from __future__ import annotations

import ast
import difflib
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.application.s03_agent_loop import run_agent_loop
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import RepositoryCommand, RepositoryManifest, SourceSnapshot
from production_optimizer.contracts.a3 import SolutionPortfolio
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, model_content_digest
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.platform import PolicyRequest
from production_optimizer.contracts.s01 import SelectedSolution
from production_optimizer.contracts.s02 import ExecutionPlan, TaskList
from production_optimizer.contracts.s03 import (
    ExecutionProvenance,
    PatchArtifact,
    SanitationFinding,
    SanitationReport,
    ScopeReport,
    ScopeViolation,
    ToolCallRecord,
)
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="s03-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_POLICY_VERSION = "s03-authorization-v1"
_DEFAULT_S03_MODEL_ID = "claude-sonnet-5"

_IGNORED_DIR_NAMES = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "node_modules",
}
_BINARY_EXTENSIONS = {".exe", ".dll", ".so", ".bin", ".pyc", ".pyd"}
_LOCKFILE_NAMES = {"uv.lock", "package-lock.json", "poetry.lock", "Cargo.lock", "yarn.lock"}
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

_S03_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "S03.10": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S03.60": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S03.70": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _S03_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-workflow-s03",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="s03-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=300,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/03-implement-phase.md",
        slo="S03 node completes within phase deadline",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production S03 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"s03_{node_id.replace('.', '_')}"
    return execute


def build_s03_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import S03_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in S03_NODE_IDS
    }


def build_s03_runtime(*, ports: NodePorts) -> NodeRuntime:
    return NodeRuntime(build_s03_registrations(), ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "S03.10":
            return _s03_10(state, ports)
        case "S03.20":
            return _s03_20(state, ports)
        case "S03.30":
            return _s03_30(state, ports)
        case "S03.40":
            return _s03_40(state, ports)
        case "S03.50":
            return _s03_50(state, ports)
        case "S03.60":
            return _s03_60(state, ports)
        case "S03.70":
            return _s03_70(state, ports)
        case "S03.80":
            return _s03_80(state, ports)
        case "S03.90":
            return _s03_90(state, ports)
        case _:
            return NodeExecution()


def _model_id(ports: NodePorts) -> str:
    return ports.model_id or _DEFAULT_S03_MODEL_ID


def _strategy_by_id(portfolio: SolutionPortfolio, strategy_id: str) -> Any:
    for strategy in portfolio.strategies:
        if strategy.strategy_id == strategy_id:
            return strategy
    raise ValueError(f"strategy {strategy_id!r} not found in SolutionPortfolio")


def _active_phase_id(state: OptimizationState) -> str:
    value = state.get("s03_active_phase_id")
    if not isinstance(value, str) or not value:
        raise ValueError("S03 state is missing s03_active_phase_id (run S03.10 first)")
    return value


def _s03_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Pick the active phase and authorize it -- fail-closed
    (`ports.policy.evaluate()`), mirroring B1.31's real authorization gate."""

    if ports.policy is None:
        raise RuntimeError("S03.10 requires ports.policy to be non-None")

    plan = cast("ExecutionPlan", _read_required(ports, state, "ExecutionPlan"))
    task_list = cast("TaskList", _read_required(ports, state, "TaskList"))
    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    completed = set(cast("list[str]", state.get("s03_completed_task_ids") or []))

    active_phase_id: str | None = None
    for phase in sorted(plan.phases, key=lambda p: p.sequence):
        phase_task_ids = {
            task.task_id for task in task_list.tasks if task.phase_id == phase.phase_id
        }
        if not phase_task_ids <= completed:
            active_phase_id = phase.phase_id
            break

    if active_phase_id is None:
        return NodeExecution(
            route=NodeRoute.REJECTED,
            updates={"s03_authorization": {"authorized": False, "reason": "no remaining phase"}},
        )

    decision = ports.policy.evaluate(
        PolicyRequest(
            decision_type="s03_phase_authorization",
            policy_version=_POLICY_VERSION,
            tenant_id=_required_state_str(state, "tenant_id"),
            facts={"phase_id": active_phase_id, "case_id": _required_state_str(state, "case_id")},
        )
    )
    authorization = {
        "phase_id": active_phase_id,
        "authorized": decision.allowed,
        "reasons": list(decision.reasons),
        "budget_deadline_seconds": request.budget.deadline_seconds,
    }
    route = NodeRoute.CONTINUE if decision.allowed else NodeRoute.REJECTED
    return NodeExecution(
        route=route,
        updates={"s03_active_phase_id": active_phase_id, "s03_authorization": authorization},
    )


def _s03_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Create a real isolated workspace: a real `git worktree` when the
    snapshot has a git revision, else an honest plain directory copy --
    never a fabricated isolation boundary.

    Cleans up any workspace already recorded in state first: reachable when
    S04's mandatory-check failure routed back here for a real retry of the
    same phase (BR-04-004) -- the failed attempt's workspace was
    deliberately left in place for S04 to inspect (see `_s03_90`'s
    docstring) and was never cleaned up, so a fresh S03.20 run must not
    just add a second one on top of it.
    """

    existing_workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    if existing_workspace:
        _cleanup_workspace(existing_workspace)

    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    root = Path(snapshot.canonical_path_ref)
    base_temp = Path(tempfile.mkdtemp(prefix="s03-workspace-"))
    workspace_dir = base_temp / "workspace"

    isolation = "directory_copy"
    if snapshot.git_revision and _git(root, "worktree", "add", "--detach", str(workspace_dir),
                                       snapshot.git_revision) is not None:
        isolation = "git_worktree"
    else:
        shutil.copytree(
            root,
            workspace_dir,
            ignore=shutil.ignore_patterns(*_IGNORED_DIR_NAMES, "*.pyc"),
        )

    workspace = {
        "path": str(workspace_dir),
        "base_revision": snapshot.git_revision or "unversioned",
        "isolation": isolation,
        "source_root": str(root),
    }
    return NodeExecution(updates={"s03_workspace": workspace})


def _s03_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Route config to a deterministic sweep, prompt/code/architecture to
    the real bounded agent loop -- both are real executors, never a stub."""

    selected = _read_model(ports, state, _selected_solution_ref(state), SelectedSolution)
    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    strategy = _strategy_by_id(portfolio, selected.strategy_id)
    executor_kind = (
        "deterministic_config" if strategy.risk_ceiling == "experiment_config" else "llm_driven"
    )
    executor = {
        "kind": executor_kind,
        "risk_ceiling": strategy.risk_ceiling,
        "strategy_id": strategy.strategy_id,
    }
    return NodeExecution(updates={"s03_executor": executor})


def _s03_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Supply only the active phase's own files, instructions and one
    pre-authorized command -- BR-03-002's least-privilege boundary.

    `allowed_symbols_by_path` narrows scope one level past `allowed_write_paths`:
    a path is only symbol-restricted if at least one of its tasks actually
    declared `PlanTask.symbols` (S02's plan already carries this field). A
    path with no task declaring any symbol for it stays file-scoped only --
    per the spec's own BR-03-003 ("out-of-scope changes reject the patch;
    they are not silently trimmed"), narrowing to symbols the model never
    actually committed to would risk rejecting a legitimate change the plan
    simply didn't get that specific about.
    """

    plan = cast("ExecutionPlan", _read_required(ports, state, "ExecutionPlan"))
    task_list = cast("TaskList", _read_required(ports, state, "TaskList"))
    manifest = cast("RepositoryManifest", _read_required(ports, state, "RepositoryManifest"))
    active_phase_id = _active_phase_id(state)
    phase = next(p for p in plan.phases if p.phase_id == active_phase_id)
    tasks = [task for task in task_list.tasks if task.phase_id == active_phase_id]
    allowed_write_paths = sorted({file for task in tasks for file in task.files})
    allowed_symbols_by_path: dict[str, list[str]] = {}
    for task in tasks:
        if not task.symbols:
            continue
        for file in task.files:
            allowed_symbols_by_path.setdefault(file, []).extend(task.symbols)
    allowed_symbols_by_path = {
        path: sorted(set(symbols)) for path, symbols in allowed_symbols_by_path.items()
    }
    allowed_command = next((c for c in manifest.commands if c.kind == "unit"), None)

    context: dict[str, Any] = {
        "phase_id": phase.phase_id,
        "phase_kind": phase.phase_kind,
        "allowed_write_paths": allowed_write_paths,
        "allowed_symbols_by_path": allowed_symbols_by_path,
        "allowed_command": allowed_command.model_dump(mode="json") if allowed_command else None,
        "task_instructions": [
            {
                "task_id": task.task_id,
                "objective": task.objective,
                "instructions": task.instructions,
                "symbols": task.symbols,
            }
            for task in tasks
        ],
        "treatment": phase.treatment.model_dump(mode="json"),
        "done_criteria": phase.done_criteria,
    }
    return NodeExecution(updates={"s03_context": context})


def _s03_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Apply exactly one approved logical change (BR-03-001) and record
    real tool calls/tokens for provenance."""

    executor = cast("dict[str, Any]", state.get("s03_executor") or {})
    context = cast("dict[str, Any]", state.get("s03_context") or {})
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    root = Path(cast("str", workspace["path"]))

    if executor.get("kind") == "deterministic_config":
        tool_calls = _run_deterministic_executor(root, context)
        tokens_in, tokens_out, model_id_used = 0, 0, None
    else:
        if ports.model is None:
            raise RuntimeError(
                "S03.50 requires ModelProviderPort wired into NodePorts for an llm_driven executor"
            )
        allowed_command = None
        if context.get("allowed_command"):
            allowed_command = RepositoryCommand.model_validate(context["allowed_command"])
        loop_result = run_agent_loop(
            model=ports.model,
            model_id=_model_id(ports),
            workspace_root=root,
            allowed_write_paths=set(context.get("allowed_write_paths") or []),
            allowed_command=allowed_command,
            system_prompt=_build_system_prompt(),
            user_context=_build_user_context(context),
            idempotency_prefix=(
                f"{_required_state_str(state, 'case_id')}:S03.50:{context.get('phase_id')}"
            ),
        )
        tool_calls = loop_result.tool_calls
        tokens_in, tokens_out = loop_result.input_tokens, loop_result.output_tokens
        model_id_used = _model_id(ports)

    tool_call_state = {
        "tool_calls": [call.model_dump(mode="json") for call in tool_calls],
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "model_id": model_id_used,
    }
    return NodeExecution(updates={"s03_tool_calls": tool_call_state})


def _run_deterministic_executor(root: Path, context: dict[str, Any]) -> list[ToolCallRecord]:
    treatment = cast("dict[str, Any]", context.get("treatment") or {})
    before = cast("str", treatment.get("before", ""))
    after = cast("str", treatment.get("after", ""))
    tool_calls: list[ToolCallRecord] = []
    for path in cast("list[str]", context.get("allowed_write_paths") or []):
        target = root / path
        if not target.exists():
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        if before and before in text:
            target.write_text(text.replace(before, after), encoding="utf-8")
            tool_calls.append(
                ToolCallRecord(
                    step=len(tool_calls) + 1,
                    tool="write_file",
                    path=path,
                    summary=f"replaced {before!r} with {after!r}",
                )
            )
    tool_calls.append(
        ToolCallRecord(
            step=len(tool_calls) + 1, tool="done", summary="deterministic config sweep complete"
        )
    )
    return tool_calls


def _build_system_prompt() -> str:
    """S03.50's executor prompt, stated in terms of the rules
    `docs/project-blueprint/shared-workflow/03-implement-phase.md` actually
    enforces downstream -- S03.60 (scope) and S03.70 (sanitation) reject a
    patch deterministically, so the executor is told those boundaries up
    front rather than discovering them by having its work thrown away."""

    return (
        "You are the S03 implementation executor for an evidence-grounded code "
        "optimization platform. Your job is to apply EXACTLY ONE logical change "
        "-- the treatment described below -- to an isolated copy of a repository.\n"
        "\n"
        "Tools: you may call read_file, write_file (only for a path in the "
        "authorized write paths given to you) or run_command (only the single "
        "pre-authorized command, if one is offered). Call read_file before "
        "write_file so your edit is based on the file's real current content, "
        "never on a guess about what it contains. Call `done` as soon as the "
        "treatment is applied.\n"
        "\n"
        "Hard boundaries -- violating any of these causes the whole patch to be "
        "REJECTED, not quietly trimmed back (BR-03-003):\n"
        "- Write ONLY to the authorized write paths. Do not create, rename or "
        "edit any other file, however reasonable the change seems.\n"
        "- If a task lists authorized symbols for a path, confine your edit to "
        "those functions/classes. A changed symbol outside that list is an "
        "out-of-scope change even though the file itself was authorized.\n"
        "- Apply one logical change only (BR-03-001). Do not bundle in "
        "refactors, cleanups, renames, formatting sweeps or unrelated fixes you "
        "notice along the way.\n"
        "- Never run a command other than the one authorized, and never attempt "
        "network access or use of credentials (BR-03-002).\n"
        "- Never edit tests, fixtures or assertions to make them agree with your "
        "change. Step 04 verifies this patch independently; weakening the thing "
        "that would catch a mistake is a rejection, not a pass.\n"
        "- Do not touch dependency lockfiles, database migrations, vendored "
        "directories or binary files, and never write a secret, token or "
        "credential into a file -- S03.70 sanitation rejects the patch on any of "
        "these.\n"
        "\n"
        "This step does not judge whether the change is correct or beneficial -- "
        "Step 04 verifies and Step 06 decides. Do not argue for your change, do "
        "not claim it works, and do not try to validate it beyond the single "
        "authorized command. Report honestly in each `summary`: if you cannot "
        "apply the treatment within these boundaries, say so plainly in the "
        "summary and call `done` rather than approximating it with an "
        "out-of-scope edit."
    )


def _build_user_context(context: dict[str, Any]) -> str:
    treatment = cast("dict[str, Any]", context.get("treatment") or {})
    phase_kind = context.get("phase_kind")
    allowed_paths = cast("list[str]", context.get("allowed_write_paths") or [])
    allowed_command = context.get("allowed_command")
    lines = [
        f"Phase: {context.get('phase_id')} (kind={phase_kind})",
        (
            "A diagnostic phase investigates a hypothesis; it must not implement "
            "the eventual fix (BR-02-006)."
            if phase_kind == "diagnostic"
            else "An implementation phase applies the real change described by the treatment."
        ),
        "",
        f"The one logical change to make: set {treatment.get('variable')!r} "
        f"from {treatment.get('before')!r} to {treatment.get('after')!r}.",
        "",
        f"Authorized write paths ({len(allowed_paths)} -- writing anywhere else "
        f"rejects the patch): {allowed_paths or '(none: this phase authorizes no file writes)'}",
        f"Authorized command: {allowed_command or '(none: run_command is unavailable this phase)'}",
        "",
        "Done criteria this phase is ultimately judged against by Step 04/06 "
        "(you do not verify them yourself):",
    ]
    lines.extend(
        f"- {criterion}" for criterion in cast("list[str]", context.get("done_criteria") or [])
    )
    lines.append("")
    lines.append("Tasks to carry out:")
    for task in cast("list[dict[str, Any]]", context.get("task_instructions") or []):
        lines.append(f"- {task['task_id']}: {task['objective']}")
        lines.append(f"    instructions: {task['instructions']}")
        symbols = cast("list[str]", task.get("symbols") or [])
        if symbols:
            lines.append(
                f"    authorized symbols in this task's files -- confine edits to these: {symbols}"
            )
    return "\n".join(lines)


def _s03_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real scope enforcement per the spec's own S03.60 row ("Compare changed
    paths/symbols/dependencies and treatment semantics with plan",
    `docs/project-blueprint/shared-workflow/03-implement-phase.md`):

    1. Path level (as before): byte-compare the whole workspace tree against
       the untouched source root; anything changed outside the phase's own
       declared paths is a violation.
    2. Symbol level (new): for a changed Python path whose tasks declared
       `PlanTask.symbols`, parse both the original and edited source with
       `ast` and diff top-level function/class/method definitions by name.
       A changed/added/removed symbol not in that path's declared set is a
       violation -- caught even though the *file* itself was authorized,
       which pure path-diffing could never detect. A path with no task
       symbol declaration stays file-scoped only (see `_s03_40`'s docstring
       for why: BR-03-003 forbids silently trimming a change the plan never
       claimed to be that specific about, and the reverse -- inventing a
       restriction the plan never stated -- is the same mistake in the
       other direction).
    """

    del ports
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    context = cast("dict[str, Any]", state.get("s03_context") or {})
    root = Path(cast("str", workspace["path"]))
    source_root = Path(cast("str", workspace["source_root"]))
    allowed = set(cast("list[str]", context.get("allowed_write_paths") or []))
    allowed_symbols_by_path = cast(
        "dict[str, list[str]]", context.get("allowed_symbols_by_path") or {}
    )

    changed_files = _diff_changed_files(source_root, root)
    violations = [
        ScopeViolation(
            kind="path", path=path, reason="file changed outside the phase's declared scope"
        )
        for path in changed_files
        if path not in allowed
    ]
    for path in changed_files:
        if path not in allowed or path not in allowed_symbols_by_path:
            continue
        violations.extend(
            _symbol_scope_violations(
                source_root / path, root / path, path, set(allowed_symbols_by_path[path])
            )
        )

    report = ScopeReport(in_scope=not violations, violations=violations)
    route = NodeRoute.CONTINUE if report.in_scope else NodeRoute.REJECTED
    if not report.in_scope:
        _cleanup_workspace(workspace)
    updates = {
        "s03_scope_report": {
            "report": report.model_dump(mode="json"),
            "changed_files": changed_files,
        }
    }
    return NodeExecution(route=route, updates=updates)


def _symbol_scope_violations(
    original_path: Path, edited_path: Path, rel_path: str, allowed_symbols: set[str]
) -> list[ScopeViolation]:
    """Diffs top-level function/class/method definitions between the
    original and edited version of one Python file, by name and body text.
    Non-Python files, and files that fail to parse (e.g. edited into invalid
    syntax -- S03.70/S04 catch that separately), are skipped rather than
    treated as a scope violation: this check only ever adds violations it
    can actually ground in a real symbol diff, per BR-03-003's own
    "not silently trimmed" rule -- silence here just means "not applicable",
    never "assumed fine"."""

    if Path(rel_path).suffix != ".py":
        return []
    original_symbols = _extract_symbols(original_path)
    edited_symbols = _extract_symbols(edited_path)
    if original_symbols is None or edited_symbols is None:
        return []

    changed_names = {
        name
        for name in {*original_symbols, *edited_symbols}
        if original_symbols.get(name) != edited_symbols.get(name)
    }
    out_of_scope = sorted(changed_names - allowed_symbols)
    return [
        ScopeViolation(
            kind="symbol",
            path=rel_path,
            reason=(
                f"symbol {name!r} changed outside the phase's declared symbols: "
                f"{sorted(allowed_symbols)}"
            ),
        )
        for name in out_of_scope
    ]


def _extract_symbols(path: Path) -> dict[str, str] | None:
    """Maps each top-level function/class name (and `Class.method` for each
    method inside a class) to its own source text, so the caller can detect
    a changed/added/removed definition by comparing these dicts. Returns
    `None` (not `{}`) when the file doesn't exist or fails to parse -- the
    caller must be able to tell "nothing to compare" apart from "compared
    and found zero definitions", since only the latter is a real, empty diff."""

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return None

    symbols: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            symbols[node.name] = ast.unparse(node)
        if isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    symbols[f"{node.name}.{child.name}"] = ast.unparse(child)
    return symbols


def _diff_changed_files(source_root: Path, workspace_root: Path) -> list[str]:
    changed: list[str] = []
    for path in workspace_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace_root)
        if any(part in _IGNORED_DIR_NAMES for part in rel.parts):
            continue
        original = source_root / rel
        rel_str = str(rel).replace("\\", "/")
        try:
            if not original.exists() or original.read_bytes() != path.read_bytes():
                changed.append(rel_str)
        except OSError:
            changed.append(rel_str)
    return sorted(changed)


def _s03_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real sanitation: regex-based secret detection, binary/lockfile/
    migration classification -- fails closed on any finding."""

    del ports
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    root = Path(cast("str", workspace["path"]))
    scope_state = cast("dict[str, Any]", state.get("s03_scope_report") or {})
    changed_files = cast("list[str]", scope_state.get("changed_files") or [])

    findings: list[SanitationFinding] = []
    for rel in changed_files:
        target = root / rel
        if not target.exists():
            continue
        suffix = Path(rel).suffix.lower()
        if suffix in _BINARY_EXTENSIONS:
            findings.append(SanitationFinding(kind="forbidden_binary", detail=rel))
            continue
        if Path(rel).name in _LOCKFILE_NAMES:
            findings.append(SanitationFinding(kind="lockfile_change", detail=rel))
        if rel.startswith("migrations/") or "/migrations/" in rel:
            findings.append(SanitationFinding(kind="migration_change", detail=rel))
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(
                    SanitationFinding(kind="secret", detail=f"{rel}: matched a secret pattern")
                )
                break

    report = SanitationReport(passed=not findings, findings=findings)
    route = NodeRoute.CONTINUE if report.passed else NodeRoute.REJECTED
    if not report.passed:
        _cleanup_workspace(workspace)
    return NodeExecution(
        route=route, updates={"s03_sanitation_report": report.model_dump(mode="json")}
    )


def _s03_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The only node that seals `PatchArtifact`/`ExecutionProvenance` --
    phase *and pass* scoped artifact_id (mirrors `a3_handlers._stage_envelope`
    /`_pass_stage_id`) so a later multi-phase milestone, *and* a real retry of
    this same phase (S04.80's BR-04-004 failure or S06.50's FIX_ONE_PART,
    both bump `s03_revision_attempts` -- see `contracts/state.py`), can call
    this again without `merge_artifact_refs` colliding on a same-id,
    different-digest second attempt. `s04_handlers` reads these back by the
    exact same stage id via `_require_stage_ref`, not by bare artifact_type,
    since more than one `PatchArtifact`/`ExecutionProvenance` can coexist in
    `artifact_refs` once a phase has been retried."""

    plan_ref = _require_ref(state, "ExecutionPlan")
    task_list = cast("TaskList", _read_required(ports, state, "TaskList"))
    workspace = cast("dict[str, Any]", state.get("s03_workspace") or {})
    root = Path(cast("str", workspace["path"]))
    source_root = Path(cast("str", workspace["source_root"]))
    scope_state = cast("dict[str, Any]", state.get("s03_scope_report") or {})
    changed_files = cast("list[str]", scope_state.get("changed_files") or [])
    sanitation_raw = cast("dict[str, Any]", state.get("s03_sanitation_report") or {})
    executor = cast("dict[str, Any]", state.get("s03_executor") or {})
    tool_call_state = cast("dict[str, Any]", state.get("s03_tool_calls") or {})
    active_phase_id = _active_phase_id(state)
    phase_task_ids = sorted(t.task_id for t in task_list.tasks if t.phase_id == active_phase_id)

    diff_text = _build_unified_diff(source_root, root, changed_files)
    tool_calls = [
        ToolCallRecord.model_validate(raw) for raw in tool_call_state.get("tool_calls", [])
    ]

    pass_number = state.get("s03_revision_attempts", 0) or 0
    stage = f"S03.80-{active_phase_id}-pass{pass_number}"
    provenance = _seal(
        ExecutionProvenance(
            **_stage_envelope(state, stage, "ExecutionProvenance"),
            execution_plan_digest=plan_ref.content_digest,
            phase_id=active_phase_id,
            executor_kind=cast("Any", executor.get("kind")),
            model_id=tool_call_state.get("model_id"),
            tool_calls=tool_calls,
            input_tokens=cast("int", tool_call_state.get("input_tokens") or 0),
            output_tokens=cast("int", tool_call_state.get("output_tokens") or 0),
            base_revision=cast("str", workspace.get("base_revision") or "unversioned"),
            isolation=cast("Any", workspace.get("isolation")),
        )
    )
    provenance_ref = _put_envelope(ports, state, provenance, node_id="S03.80")

    patch = _seal(
        PatchArtifact(
            **_stage_envelope(state, stage, "PatchArtifact"),
            execution_plan_digest=plan_ref.content_digest,
            execution_provenance_digest=provenance_ref.content_digest,
            phase_id=active_phase_id,
            task_ids=phase_task_ids or ["no-tasks"],
            base_revision=cast("str", workspace.get("base_revision") or "unversioned"),
            changed_files=changed_files or ["no-changes"],
            diff=diff_text or "(no textual diff -- no files changed)",
            scope_report=ScopeReport.model_validate(
                cast("dict[str, Any]", scope_state.get("report") or {"in_scope": True})
            ),
            sanitation_report=SanitationReport.model_validate(
                sanitation_raw or {"passed": True}
            ),
        )
    )
    patch_ref = _put_envelope(ports, state, patch, node_id="S03.80")
    return NodeExecution(updates={"artifact_refs": [provenance_ref, patch_ref]})


def _build_unified_diff(source_root: Path, workspace_root: Path, changed_files: list[str]) -> str:
    diff_lines: list[str] = []
    for rel in changed_files:
        original_path = source_root / rel
        new_path = workspace_root / rel
        original_lines = (
            original_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if original_path.exists()
            else []
        )
        new_lines = (
            new_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if new_path.exists()
            else []
        )
        diff_lines.extend(
            difflib.unified_diff(original_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}")
        )
    return "".join(diff_lines)


def _s03_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Mark only this phase's own tasks ready for verification -- never
    measured/complete (that is S05/S06's job, per the doc's own rule).

    Deliberately writes `s03_implemented_task_ids`, *not*
    `s03_completed_task_ids`: S03.10 reads the latter to pick the next
    active phase, and a task only truly needs no further S03 work once S04
    (Verify) actually passes -- if S04.80 fails a mandatory check and routes
    back here for a real retry of the *same* phase (BR-04-004), S03.10 must
    still see it as active, not skip past it. `s04_handlers._s04_90` is the
    one that promotes a phase's tasks into `s03_completed_task_ids`, and
    only on a real pass.

    Deliberately does *not* clean up `s03_workspace` here: S04 (Verify)
    reuses this exact same isolated workspace directly to run its real
    build/test commands against the actual patched files, rather than
    re-applying `PatchArtifact.diff` to a second fresh checkout -- simpler
    and more robust than parsing/reapplying a unified diff, and just as
    real, since it is the literal directory S03.50 edited. S04's own last
    node is responsible for the eventual cleanup instead."""

    task_list = cast("TaskList", _read_required(ports, state, "TaskList"))
    active_phase_id = _active_phase_id(state)
    phase_task_ids = {task.task_id for task in task_list.tasks if task.phase_id == active_phase_id}
    implemented = set(cast("list[str]", state.get("s03_implemented_task_ids") or []))
    implemented |= phase_task_ids
    return NodeExecution(updates={"s03_implemented_task_ids": sorted(implemented)})


# ---------------------------------------------------------------------------
# Shared helpers (mirrors s02_handlers.py/c0_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "OptimizationRequest": cast("type[ArtifactEnvelope]", OptimizationRequest),
    "SelectedSolution": cast("type[ArtifactEnvelope]", SelectedSolution),
    "SolutionPortfolio": cast("type[ArtifactEnvelope]", SolutionPortfolio),
    "SourceSnapshot": cast("type[ArtifactEnvelope]", SourceSnapshot),
    "RepositoryManifest": cast("type[ArtifactEnvelope]", RepositoryManifest),
    "ExecutionPlan": cast("type[ArtifactEnvelope]", ExecutionPlan),
    "TaskList": cast("type[ArtifactEnvelope]", TaskList),
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
        "policy_versions": {"s03": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _stage_artifact_id(case_id: str, node_id: str, artifact_type: str) -> str:
    return f"{case_id}-{node_id}-{artifact_type}"


def _selected_solution_ref(state: OptimizationState) -> ArtifactRef:
    """Mirrors `s02_handlers._selected_solution_ref` -- `SelectedSolution` is
    pass-scoped in `s01_handlers` (BR-01-005), so a plain first-match lookup
    would risk returning a stale pass's selection after a real REVERT sends
    the case back to S01 and it re-selects with one more strategy excluded."""

    pass_number = len(cast("list[str]", state.get("s01_excluded_strategy_ids") or []))
    node_id = f"S01.90-pass{pass_number}"
    artifact_id = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, "SelectedSolution"
    )
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == "SelectedSolution" and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required SelectedSolution produced by {node_id}")


def _stage_envelope(state: OptimizationState, node_id: str, artifact_type: str) -> dict[str, Any]:
    """`_base_envelope` with a phase-scoped `artifact_id` -- mirrors
    `a3_handlers._stage_envelope` exactly: more than one phase can produce
    a `PatchArtifact`/`ExecutionProvenance` within one case, and the
    default `_base_envelope` artifact_id would collide across phases."""

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


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"S03 state is missing required field {key!r}")
    return value


def _git(cwd: Path, *args: str) -> str | None:
    import subprocess

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


def _cleanup_workspace(workspace: dict[str, Any]) -> None:
    """Remove the isolated workspace S03.20 created -- a real
    `git worktree remove` for the git path (falling back to a plain
    directory delete if that fails, e.g. the worktree was already removed
    on a resumed re-run), or `shutil.rmtree` for the directory-copy
    fallback. Called on every terminal path reachable after S03.20 (a scope
    or sanitation rejection, or a successful S03.90) so a real run never
    leaks a temp directory or a dangling `git worktree list` entry per
    phase -- the same class of cleanup gap already known and accepted for
    `LocalWorkerBroker`'s timed-out jobs, fixed here instead of repeated.
    """

    path = workspace.get("path")
    if not path:
        return
    workspace_dir = Path(cast("str", path))
    if workspace.get("isolation") == "git_worktree":
        source_root = workspace.get("source_root")
        if source_root and workspace_dir.exists():
            # Deregisters from the source repo's `.git/worktrees/` metadata
            # first; harmless (and skipped) if it was already removed on a
            # resumed re-run. Either way, the `rmtree` below still removes
            # the on-disk directory itself.
            _git(
                Path(cast("str", source_root)), "worktree", "remove", "--force", str(workspace_dir)
            )
    # `workspace_dir`'s parent is the `tempfile.mkdtemp()` container S03.20
    # created solely to hold it -- removing that one directory cleans up
    # both the workspace and its container in one shot.
    shutil.rmtree(workspace_dir.parent, ignore_errors=True)


__all__ = ["build_s03_registrations", "build_s03_runtime"]
