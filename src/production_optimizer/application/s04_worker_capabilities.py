"""Real `WorkerBroker` capability table for S04's build/lint/type/unit/
integration/security checks -- mirrors `a2_worker_capabilities.py`'s exact
pattern (real subprocess execution, output captured as a blob artifact),
with one necessary difference: every command runs inside S04's isolated
workspace (reused from S03, see `s03_handlers._s03_90`'s docstring), never
the command's own recorded `working_directory` (that path was resolved
against the *original*, non-isolated source tree at A2.30/31 time).
"""

from __future__ import annotations

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


def _find_command(
    artifacts: ArtifactStore, *, tenant_id: str, job: WorkerJob
) -> RepositoryCommand:
    manifest_ref = next(
        (ref for ref in job.input_refs if ref.artifact_type == "RepositoryManifest"), None
    )
    if manifest_ref is None:
        raise ValueError(f"worker job {job.job_id!r} has no RepositoryManifest input ref")

    raw = artifacts.read(tenant_id=tenant_id, ref=manifest_ref)
    payload = TypeAdapter(dict[str, Any]).validate_json(raw)
    payload.setdefault("content_digest", manifest_ref.content_digest)
    manifest = RepositoryManifest.model_validate(payload)

    command = next((c for c in manifest.commands if c.kind == job.capability), None)
    if command is None:
        raise ValueError(f"no {job.capability!r} command in RepositoryManifest")
    return command


def build_s04_capabilities(
    artifacts: ArtifactStore, *, tenant_id: str, workspace_root: Path
) -> dict[str, CapabilityFn]:
    """Capability table bound to one S04 run's isolated workspace. A fresh
    table (fresh `workspace_root`) is expected per phase/verification
    attempt -- mirrors how `build_local_command_capabilities` is
    reconstructed per A2 run rather than shared across cases."""

    def _run(job: WorkerJob) -> ArtifactRef:
        command = _find_command(artifacts, tenant_id=tenant_id, job=job)
        result = subprocess.run(
            command.argv,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=job.timeout_seconds,
            check=False,
        )
        output = {
            "command_id": command.command_id,
            "argv": command.argv,
            "working_directory": str(workspace_root),
            "exit_code": result.returncode,
            "stdout": result.stdout[-_OUTPUT_TAIL_BYTES:],
            "stderr": result.stderr[-_OUTPUT_TAIL_BYTES:],
        }
        content = canonical_json(output)
        return artifacts.put_blob(
            tenant_id=tenant_id,
            content=content,
            content_digest=sha256_digest(content),
            media_type="application/json",
        )

    return dict.fromkeys(_CHECK_KINDS, _run)


__all__ = ["CapabilityFn", "build_s04_capabilities"]
