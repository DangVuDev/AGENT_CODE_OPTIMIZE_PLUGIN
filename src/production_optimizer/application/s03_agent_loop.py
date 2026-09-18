"""S03.50's bounded, tool-constrained agent loop -- the executor for
`prompt`/`code`/`architecture` risk treatments (see
`docs/project-blueprint/shared-workflow/03-implement-phase.md`, S03.50).

Reuses `ModelProviderPort`'s existing single-shot `complete()`
(`ports/model_provider.py`) inside an application-layer loop rather than
adopting a separate agent framework (OpenHands/Claude Agent SDK/Codex): each
turn, the model may request exactly one of three tools --
`read_file`/`write_file`/`run_command` -- forced through the same
`response_schema` JSON mechanism A3 already uses for structured output.
Every tool is executed by *this module*, never by the model directly:
`write_file` is rejected outside the phase's own declared scope
(BR-03-002/BR-03-003), and `run_command` only ever runs the one
pre-authorized `RepositoryCommand` passed in, in the isolated workspace
root -- never the command's own recorded `working_directory` (that path was
resolved against the *original*, non-isolated source tree at A2.30/31 time,
and running there would defeat the isolation `s03_handlers._s03_20`
provides).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from production_optimizer.contracts.a2 import RepositoryCommand
from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelMessage,
    ModelRole,
)
from production_optimizer.contracts.s03 import ToolCallRecord
from production_optimizer.ports.model_provider import ModelProviderPort

_TOOL_LOOP_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "tool": {"type": "string", "enum": ["read_file", "write_file", "run_command", "done"]},
        "path": {"type": ["string", "null"]},
        "content": {"type": ["string", "null"]},
        "summary": {"type": "string"},
    },
    "required": ["tool", "summary"],
}

_MAX_STEPS = 8
_MAX_OUTPUT_TOKENS = 4000
_MAX_OBSERVATION_CHARS = 8000
_PROMPT_VERSION = "s03-agent-loop-v1"


@dataclass
class AgentLoopResult:
    tool_calls: list[ToolCallRecord] = field(default_factory=list[ToolCallRecord])
    input_tokens: int = 0
    output_tokens: int = 0
    stopped_reason: str = "done"


def run_agent_loop(
    *,
    model: ModelProviderPort,
    model_id: str,
    workspace_root: Path,
    allowed_write_paths: set[str],
    allowed_command: RepositoryCommand | None,
    system_prompt: str,
    user_context: str,
    idempotency_prefix: str,
    max_steps: int = _MAX_STEPS,
) -> AgentLoopResult:
    messages: list[ModelMessage] = [
        ModelMessage(role="system", content=system_prompt),
        ModelMessage(role="user", content=user_context),
    ]
    result = AgentLoopResult()
    root = workspace_root.resolve()

    for step in range(1, max_steps + 1):
        request = ModelCompletionRequest(
            role=ModelRole.GENERATOR,
            model_id=model_id,
            prompt_version=_PROMPT_VERSION,
            messages=messages,
            response_schema=_TOOL_LOOP_SCHEMA,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            idempotency_key=f"{idempotency_prefix}:step{step}",
        )
        completion = model.complete(request)
        result.input_tokens += completion.input_tokens
        result.output_tokens += completion.output_tokens

        if not completion.valid_json or completion.parsed_json is None:
            result.stopped_reason = "invalid_tool_call"
            break

        call = completion.parsed_json
        tool = call.get("tool")
        summary = str(call.get("summary") or "")

        if tool == "done":
            result.tool_calls.append(
                ToolCallRecord(
                    step=step, tool="done", summary=summary or "executor signaled done"
                )
            )
            result.stopped_reason = "done"
            break

        observation, record = _run_tool(
            step=step,
            tool=tool,
            call=call,
            summary=summary,
            root=root,
            allowed_write_paths=allowed_write_paths,
            allowed_command=allowed_command,
        )
        if record is None:
            result.stopped_reason = f"unknown_tool:{tool}"
            break
        result.tool_calls.append(record)
        messages.append(ModelMessage(role="assistant", content=json.dumps(call)))
        messages.append(ModelMessage(role="user", content=observation))
    else:
        result.stopped_reason = "budget_exhausted"

    return result


def _run_tool(
    *,
    step: int,
    tool: Any,
    call: dict[str, Any],
    summary: str,
    root: Path,
    allowed_write_paths: set[str],
    allowed_command: RepositoryCommand | None,
) -> tuple[str, ToolCallRecord | None]:
    if tool == "read_file":
        path = call.get("path")
        observation = _read_file(root, path)
        return observation, ToolCallRecord(
            step=step, tool="read_file", path=path, summary=summary or f"read {path}"
        )
    if tool == "write_file":
        path = call.get("path")
        observation = _write_file(root, path, call.get("content"), allowed_write_paths)
        return observation, ToolCallRecord(
            step=step, tool="write_file", path=path, summary=summary or f"wrote {path}"
        )
    if tool == "run_command":
        observation = _run_command(root, allowed_command)
        return observation, ToolCallRecord(
            step=step,
            tool="run_command",
            command_kind=allowed_command.kind if allowed_command else None,
            summary=summary or "ran the authorized command",
        )
    return f"error: unknown tool {tool!r}", None


def _read_file(root: Path, path: str | None) -> str:
    if not path:
        return "error: read_file requires a path"
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.exists():
        return f"error: {path} does not exist inside the workspace"
    try:
        return target.read_text(encoding="utf-8", errors="replace")[:_MAX_OBSERVATION_CHARS]
    except OSError as exc:
        return f"error: could not read {path}: {exc}"


def _write_file(root: Path, path: str | None, content: str | None, allowed: set[str]) -> str:
    if not path:
        return "error: write_file requires a path"
    if path not in allowed:
        return f"error: {path!r} is outside this phase's authorized scope: {sorted(allowed)}"
    if content is None:
        return "error: write_file requires content"
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return f"error: {path} escapes the workspace"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        # Mirrors `_read_file`'s own OSError handling: a locked/permission-
        # denied file (common on Windows -- antivirus, an editor, or a
        # still-running Docker Compose stack holding a handle into this
        # exact isolated workspace copy) is a transient environment fault,
        # not a reason to crash the whole graph. Surfacing it as an
        # observation lets the model see the failure and decide what to do
        # next (retry, pick a different path, or give up cleanly) instead of
        # an unhandled `OSError` propagating out of `NodeRuntime.execute`
        # and aborting the entire run.
        return f"error: could not write {path}: {exc}"
    return f"ok: wrote {len(content)} bytes to {path}"


def _run_command(root: Path, command: RepositoryCommand | None) -> str:
    if command is None:
        return "error: no command is authorized for this phase"
    try:
        proc = subprocess.run(
            command.argv,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: command failed to run: {exc}"
    output = f"exit_code={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return output[:_MAX_OBSERVATION_CHARS]


__all__ = ["AgentLoopResult", "run_agent_loop"]
