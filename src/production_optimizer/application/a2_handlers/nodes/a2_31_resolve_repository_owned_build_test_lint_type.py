# pyright: reportPrivateUsage=false
"""Implementation of business node A2.31."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Literal

from production_optimizer.application.a2_worker_capabilities import BENCHMARK_JSON_FILENAME
from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    RepositoryCommand,
    RepositoryManifest,
    SourceSnapshot,
    VerificationManifest,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _halt_for_command_approval,
    _propose_command_via_llm,
    _put_envelope,
    _read_model,
    _repository_interpreter,
    _require_ref,
    _seal,
)

CommandKind = Literal["build", "unit", "integration", "benchmark", "lint", "type", "security"]


def _configured_command(
    *,
    command_id: str,
    argv: list[str],
    root: Path,
    kind: CommandKind,
) -> RepositoryCommand:
    return RepositoryCommand(
        command_id=command_id,
        argv=argv,
        working_directory=str(root),
        kind=kind,
        source="pyproject_toml",
    )


def _user_declared_command(kind: CommandKind, shell_command: str, root: Path) -> RepositoryCommand:
    return RepositoryCommand(
        command_id=f"{kind}-user-declared",
        argv=shlex.split(shell_command),
        working_directory=str(root),
        kind=kind,
        source="user_declared",
    )


def _detect_repository_commands(
    request: OptimizationRequest, manifest: RepositoryManifest, root: Path
) -> tuple[list[RepositoryCommand], list[str]]:
    interpreter = _repository_interpreter(root)
    commands: list[RepositoryCommand] = []
    rejected: list[str] = []
    # `request.workload.commands` lets a requester declare a real command
    # for a repository this module's own `tool_coverage`-based convention
    # detection can't recognize (it only knows Python's pytest/ruff/mypy/
    # pytest-benchmark). A declared kind always wins over detection for that
    # same kind -- never both. Read regardless of `execution_profile`: unlike
    # the old single `command_id` (which `docker_compose` filled with a
    # non-executable `evaluation_id` placeholder -- see `a1_handlers.shared.
    # _draft_from_payload`'s docstring -- and this module had to special-case
    # around), `commands` is never auto-populated from the evaluation, so a
    # value here is always the requester's own real, explicit declaration,
    # meant for S03/S04/S05's host-side build/lint/type/unit checks
    # regardless of whether the workload is *also* measured for real inside
    # a Compose container by A2.50/S05.
    declared = request.workload.commands or {}

    if "unit" in declared:
        commands.append(_user_declared_command("unit", declared["unit"], root))
    elif manifest.tool_coverage.get("pytest", 0.0) > 0:
        commands.append(
            _configured_command(
                command_id="unit-tests", argv=[interpreter, "-m", "pytest"], root=root, kind="unit"
            )
        )
    else:
        rejected.append("unit: no pytest configuration or test root detected, and none declared")

    if "build" in declared:
        commands.append(_user_declared_command("build", declared["build"], root))
    else:
        rejected.append("build: no build command declared")

    configured_tools: tuple[tuple[str, str, CommandKind, list[str]], ...] = (
        ("ruff", "lint", "lint", [interpreter, "-m", "ruff", "check", "."]),
        ("mypy", "type-check", "type", [interpreter, "-m", "mypy", "."]),
        (
            "pytest_benchmark",
            "benchmark",
            "benchmark",
            [
                interpreter,
                "-m",
                "pytest",
                "--benchmark-only",
                f"--benchmark-json={BENCHMARK_JSON_FILENAME}",
            ],
        ),
    )
    for tool, identifier, kind, argv in configured_tools:
        if kind in declared:
            commands.append(_user_declared_command(kind, declared[kind], root))
        elif manifest.tool_coverage.get(tool, 0.0) > 0:
            commands.append(
                _configured_command(command_id=identifier, argv=argv, root=root, kind=kind)
            )
        else:
            label = (
                "pytest-benchmark dependency"
                if tool == "pytest_benchmark"
                else f"[tool.{tool}] configuration"
            )
            rejected.append(f"{kind}: no {label} detected, and none declared")
    return commands, rejected


def _resolve_llm_fallback(
    state: OptimizationState,
    ports: NodePorts,
    snapshot: SourceSnapshot,
    manifest: RepositoryManifest,
    rejected: list[str],
) -> list[RepositoryCommand] | NodeExecution:
    resume_command = state.get("resume_command")
    if resume_command is not None:
        proposal = state.get("pending_command_proposal")
        if resume_command.decision == "approve" and proposal is not None:
            return [RepositoryCommand.model_validate(proposal)]
        rejected.append(f"llm_suggested: resumed with decision {resume_command.decision!r}")
        return []
    if ports.model is None:
        rejected.append("llm_suggested: no ModelProviderPort wired into A2")
        return []
    proposal = _propose_command_via_llm(ports, state, snapshot, manifest)
    if proposal is None:
        rejected.append("llm_suggested: model did not return a usable proposal")
        return []
    return _halt_for_command_approval(state, ports, proposal)


def handle_a2_31_resolve_repository_owned_build_test_lint_type(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    manifest = _read_model(
        ports, state, _require_ref(state, "RepositoryManifest"), RepositoryManifest
    )
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    root = Path(snapshot.canonical_path_ref)
    commands, rejected = _detect_repository_commands(request, manifest, root)

    # Highest-trust signal: the requester already told A1 exactly which
    # commands to run, per kind (`ManualCasePayload.commands` ->
    # `WorkloadContract.commands`, e.g. {"unit": "go test ./...", "build":
    # "go build ./..."}). This is what lets a repository this module's own
    # `tool_coverage`-based detection can't recognize (pytest/ruff/mypy are
    # Python-only conventions) still get real, runnable checks for S04/S05
    # instead of an empty `VerificationManifest.commands` that would later
    # crash S04.90's `VerificationReport(check_results=...)` seal (that
    # model requires at least one result -- see `contracts/s04.py`).
    # Detecting/guessing a command below is only ever a fallback for a kind
    # the requester never declared -- a repository convention is never as
    # reliable as the requester's own explicit statement of intent. This
    # applies under `docker_compose` too: `workload.commands` and
    # `execution.evaluations[].command` are two separate, independent
    # channels (host-side build/lint/type/unit checks vs. the in-container
    # evaluation A2.50 dispatches as a `compose_evaluation` job) -- a
    # requester declaring one says nothing about the other, so neither
    # profile should silently discard `workload.commands`.
    # Last resort: nothing real was detected or declared at all -- ask the
    # model what it would run, but never execute an LLM guess without a
    # human approving it first (unlike every command above, which are real,
    # declared-or-detected, never guessed).
    if not commands:
        fallback = _resolve_llm_fallback(state, ports, snapshot, manifest, rejected)
        if isinstance(fallback, NodeExecution):
            return fallback
        commands = fallback

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


__all__ = ["handle_a2_31_resolve_repository_owned_build_test_lint_type"]
