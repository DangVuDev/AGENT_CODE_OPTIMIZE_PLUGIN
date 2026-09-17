"""Real `WorkerBroker` capability table for S04's build/lint/type/unit/
integration/security checks -- mirrors `a2_worker_capabilities.py`'s exact
pattern (real subprocess execution, output captured as a blob artifact),
with one necessary difference: every command runs inside S04's isolated
workspace (reused from S03, see `s03_handlers._s03_90`'s docstring), never
the command's own recorded `working_directory` (that path was resolved
against the *original*, non-isolated source tree at A2.30/31 time).

`kind="unit"` additionally appends real `pytest-cov` flags and parses a
real coverage percentage out of the command's own stdout when the manifest
declares the repository actually has `pytest-cov` (`tool_coverage`, set at
A2.30) AND the unit command is actually a pytest invocation (`_is_pytest_
invocation`) -- never when either isn't true, per the spec's own S04.90 row
("Store commands, outputs, versions, durations, coverage and decision") and
this codebase's "honest unavailable, never fabricated" convention
(`s04_handlers._s04_60`, B1.32-35). The pytest-invocation check exists
because `RepositoryCommand` carries no dedicated test-framework field: a
repository that declares `pytest-cov` for one test suite but whose
requester-declared `kind="unit"` command is something else entirely (e.g.
`npm test`) must never have `--cov` flags corrupt that unrelated command.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from production_optimizer.contracts.a2 import RepositoryCommand, RepositoryManifest
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.platform import WorkerJob
from production_optimizer.ports.artifacts import ArtifactStore

CapabilityFn = Callable[[WorkerJob], ArtifactRef]

_OUTPUT_TAIL_BYTES = 8000
_CHECK_KINDS = ("build", "lint", "type", "unit", "integration", "security")

# `pytest-cov`'s own terminal report ends with a line shaped like
# "TOTAL      120     18    85%" -- the percentage is the real signal;
# everything else on that line is per-file detail this check doesn't need.
_COVERAGE_TOTAL_LINE = re.compile(r"^TOTAL\s+.*?(\d+)%\s*$", re.MULTILINE)


def _find_manifest(
    artifacts: ArtifactStore, *, tenant_id: str, job: WorkerJob
) -> RepositoryManifest:
    manifest_ref = next(
        (ref for ref in job.input_refs if ref.artifact_type == "RepositoryManifest"), None
    )
    if manifest_ref is None:
        raise ValueError(f"worker job {job.job_id!r} has no RepositoryManifest input ref")

    raw = artifacts.read(tenant_id=tenant_id, ref=manifest_ref)
    payload = TypeAdapter(dict[str, Any]).validate_json(raw)
    payload.setdefault("content_digest", manifest_ref.content_digest)
    return RepositoryManifest.model_validate(payload)


def _find_command(manifest: RepositoryManifest, *, kind: str) -> RepositoryCommand:
    command = next((c for c in manifest.commands if c.kind == kind), None)
    if command is None:
        raise ValueError(f"no {kind!r} command in RepositoryManifest")
    return command


def _is_pytest_invocation(argv: list[str]) -> bool:
    """Real, argv-based check -- `RepositoryCommand` carries no dedicated
    test-framework field, so this is the only honest signal available.
    Scans the whole argv (not just position 0/1) for a `"pytest"` token so
    wrapper invocations (`uv run pytest`, `tox`, a shell script that calls
    pytest internally being the one real exception this can't see into)
    are still recognized, while a genuinely different unit command (`npm
    test`, a custom script) is correctly left alone even when the
    repository happens to also declare `pytest-cov` for an unrelated test
    suite."""

    return any(Path(part).stem.lower() == "pytest" for part in argv)


def _coverage_argv(
    command: RepositoryCommand, manifest: RepositoryManifest
) -> tuple[list[str], bool]:
    """Appends real `pytest-cov` flags to a real unit-test command, only
    when the repository actually declares the dependency AND the command
    is actually a pytest invocation -- `--cov=.` (the whole workspace, not
    a guessed module name) keeps this from ever silently under-reporting
    coverage for a module `RepositoryManifest.modules` didn't happen to
    name correctly. Returns whether coverage was actually requested
    alongside the argv, rather than leaving the caller to infer it via
    identity comparison against the original argv (fragile against a
    future refactor that always returns a new list)."""

    if manifest.tool_coverage.get("pytest_cov", 0.0) <= 0:
        return command.argv, False
    if not _is_pytest_invocation(command.argv):
        return command.argv, False
    return [*command.argv, "--cov=.", "--cov-report=term"], True


def _parse_coverage_percent(stdout: str) -> float | None:
    match = _COVERAGE_TOTAL_LINE.search(stdout)
    return float(match.group(1)) if match else None


def build_s04_capabilities(
    artifacts: ArtifactStore, *, tenant_id: str, workspace_root: Path
) -> dict[str, CapabilityFn]:
    """Capability table bound to one S04 run's isolated workspace. A fresh
    table (fresh `workspace_root`) is expected per phase/verification
    attempt -- mirrors how `build_local_command_capabilities` is
    reconstructed per A2 run rather than shared across cases."""

    def _run(job: WorkerJob) -> ArtifactRef:
        manifest = _find_manifest(artifacts, tenant_id=tenant_id, job=job)
        command = _find_command(manifest, kind=job.capability)
        if job.capability == "unit":
            argv, coverage_requested = _coverage_argv(command, manifest)
        else:
            argv, coverage_requested = command.argv, False
        result = subprocess.run(
            argv,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=job.timeout_seconds,
            check=False,
        )
        output: dict[str, Any] = {
            "command_id": command.command_id,
            "argv": argv,
            "working_directory": str(workspace_root),
            "exit_code": result.returncode,
            "stdout": result.stdout[-_OUTPUT_TAIL_BYTES:],
            "stderr": result.stderr[-_OUTPUT_TAIL_BYTES:],
        }
        if coverage_requested:
            output["coverage_percent"] = _parse_coverage_percent(result.stdout)
        content = canonical_json(output)
        return artifacts.put_blob(
            tenant_id=tenant_id,
            content=content,
            content_digest=sha256_digest(content),
            media_type="application/json",
        )

    return dict.fromkeys(_CHECK_KINDS, _run)


__all__ = ["CapabilityFn", "build_s04_capabilities"]
