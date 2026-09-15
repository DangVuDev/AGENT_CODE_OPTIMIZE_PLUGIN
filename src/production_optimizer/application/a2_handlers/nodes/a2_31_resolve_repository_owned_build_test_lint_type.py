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


def _detect_repository_commands(
    request: OptimizationRequest, manifest: RepositoryManifest, root: Path
) -> tuple[list[RepositoryCommand], list[str]]:
    interpreter = _repository_interpreter(root)
    commands: list[RepositoryCommand] = []
    rejected: list[str] = []
    command_id = request.workload.command_id if request.execution is None else None
    if command_id:
        commands.append(
            RepositoryCommand(
                command_id=command_id,
                argv=shlex.split(command_id),
                working_directory=str(root),
                kind="unit",
                source="user_declared",
            )
        )
    elif manifest.tool_coverage.get("pytest", 0.0) > 0:
        commands.append(
            _configured_command(
                command_id="unit-tests", argv=[interpreter, "-m", "pytest"], root=root, kind="unit"
            )
        )
    else:
        rejected.append("unit: no pytest configuration or test root detected")

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
        if manifest.tool_coverage.get(tool, 0.0) > 0:
            commands.append(
                _configured_command(command_id=identifier, argv=argv, root=root, kind=kind)
            )
        else:
            label = (
                "pytest-benchmark dependency"
                if tool == "pytest_benchmark"
                else f"[tool.{tool}] configuration"
            )
            rejected.append(f"{kind}: no {label} detected")
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
    # command to measure (`ManualCasePayload.command_id` ->
    # `WorkloadContract.command_id`). Detecting/guessing a command below is
    # only ever a fallback for when this was never declared -- a repository
    # convention is never as reliable as the requester's own explicit
    # statement of intent.
    #
    # Only in the legacy-discovery profile, though: under `docker_compose`,
    # `a1_handlers._draft_from_payload` deliberately fills `command_id` with
    # the *evaluation_id* of the first `EvaluationSpec` (a logical
    # identifier, e.g. "checkout-evaluation"), not a shell command -- the
    # real argv lives in `execution.evaluations[].command` and is dispatched
    # by A2.50 as a `compose_evaluation` job. Treating that identifier as an
    # executable would try to spawn a binary that does not exist.
    # Last resort: nothing real was detected at all (no explicit command_id,
    # no known pyproject.toml convention) -- ask the model what it would run,
    # but never execute an LLM guess without a human approving it first
    # (unlike every command above, which are real, detected-not-guessed).
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
