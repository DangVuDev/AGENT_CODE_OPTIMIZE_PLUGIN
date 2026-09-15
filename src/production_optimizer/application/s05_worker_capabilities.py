"""Real `WorkerBroker` capability for S05's remeasurement run -- reuses
S03's own isolated (already-patched) workspace exactly like
`s04_worker_capabilities.py`, and reuses A2's own pytest-benchmark parsing
convention (`a2_worker_capabilities.py`'s `_run_benchmark`) rather than
inventing a second one: same JSON filename convention, same raw-round
extraction, same sample cap.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from production_optimizer.application.a2_worker_capabilities import BENCHMARK_JSON_FILENAME
from production_optimizer.contracts.a2 import RepositoryCommand, RepositoryManifest
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.platform import WorkerJob
from production_optimizer.ports.artifacts import ArtifactStore

CapabilityFn = Callable[[WorkerJob], ArtifactRef]

_OUTPUT_TAIL_BYTES = 8000
# Mirrors `a2_worker_capabilities._MAX_BENCHMARK_SAMPLES` exactly (kept as
# its own module-local constant rather than importing that private name --
# see this codebase's self-contained-handler-file convention).
_MAX_BENCHMARK_SAMPLES = 10


def _find_command(artifacts: ArtifactStore, *, tenant_id: str, job: WorkerJob) -> RepositoryCommand:
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


def build_s05_capabilities(
    artifacts: ArtifactStore, *, tenant_id: str, workspace_root: Path
) -> dict[str, CapabilityFn]:
    """Capability table bound to one S05 remeasurement run's real, already-
    patched workspace. A fresh table is expected per phase/pass, mirroring
    `s04_worker_capabilities.build_s04_capabilities`."""

    def _run_benchmark(job: WorkerJob) -> ArtifactRef:
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

        json_path = workspace_root / BENCHMARK_JSON_FILENAME
        rounds: list[dict[str, Any]] = []
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                for bench in data.get("benchmarks", []):
                    name = bench.get("name")
                    for value in bench.get("stats", {}).get("data", [])[:_MAX_BENCHMARK_SAMPLES]:
                        rounds.append({"name": name, "value": value, "unit": "seconds"})
            except (OSError, json.JSONDecodeError, AttributeError, TypeError):
                rounds = []
            finally:
                json_path.unlink(missing_ok=True)

        output = {
            "command_id": command.command_id,
            "argv": command.argv,
            "working_directory": str(workspace_root),
            "exit_code": result.returncode,
            "stdout": result.stdout[-_OUTPUT_TAIL_BYTES:],
            "stderr": result.stderr[-_OUTPUT_TAIL_BYTES:],
            "benchmark_rounds": rounds,
        }
        content = canonical_json(output)
        return artifacts.put_blob(
            tenant_id=tenant_id,
            content=content,
            content_digest=sha256_digest(content),
            media_type="application/json",
        )

    return {"benchmark": _run_benchmark}


__all__ = ["CapabilityFn", "build_s05_capabilities"]
