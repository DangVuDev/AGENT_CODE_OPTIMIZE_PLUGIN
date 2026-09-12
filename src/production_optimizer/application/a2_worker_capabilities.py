from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from production_optimizer.contracts.a2 import RepositoryCommand, VerificationManifest
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.platform import WorkerJob
from production_optimizer.ports.artifacts import ArtifactStore

CapabilityFn = Callable[[WorkerJob], ArtifactRef]

_OUTPUT_TAIL_BYTES = 8000

# A2.31 bakes this exact relative filename into a "benchmark"-kind
# `RepositoryCommand.argv` (as `--benchmark-json=<name>`) so the command's
# full argv stays a deterministic, sealed fact -- `_run_benchmark` never adds
# a flag A2.31/A2.50 didn't already authorize. Written under the target
# repo's own working directory by pytest-benchmark itself, then deleted
# after being read back, matching the file-output-tool pattern (no artifact
# left behind in the caller's repo).
BENCHMARK_JSON_FILENAME = ".optimizer-benchmark-result.json"

# Cap on how many of pytest-benchmark's own calibrated rounds become
# `EvidenceItem`s. A sub-microsecond function legitimately calibrates to
# 100k+ rounds within pytest-benchmark's default 1s `--benchmark-max-time` --
# real, independently-timed samples, but far more than any evidence
# requirement needs (today's `minimum_samples` values are 1 or 3), and
# turning all of them into Pydantic models would build/serialize/hash one
# per round through every later A2 stage.
_MAX_BENCHMARK_SAMPLES = 10


def _find_command(
    artifacts: ArtifactStore, *, tenant_id: str, job: WorkerJob
) -> RepositoryCommand:
    manifest_ref = next(
        (ref for ref in job.input_refs if ref.artifact_type == "VerificationManifest"),
        None,
    )
    if manifest_ref is None:
        raise ValueError(f"worker job {job.job_id!r} has no VerificationManifest input ref")

    raw = artifacts.read(tenant_id=tenant_id, ref=manifest_ref)
    payload = TypeAdapter(dict[str, Any]).validate_json(raw)
    payload.setdefault("content_digest", manifest_ref.content_digest)
    manifest = VerificationManifest.model_validate(payload)

    command = next((c for c in manifest.commands if c.kind == job.capability), None)
    if command is None:
        raise ValueError(f"no {job.capability!r} command in VerificationManifest")
    return command


def build_local_command_capabilities(
    artifacts: ArtifactStore, *, tenant_id: str
) -> dict[str, CapabilityFn]:
    """Real `WorkerBroker` capability table for A2's repository-owned commands.

    Each returned callable runs the exact argv/working_directory that A2.31
    detected and A2.50 authorized — never a fabricated or inferred command.
    A job submitted by `_a2_50` carries its `VerificationManifest` artifact
    ref in `input_refs`; this reads it back and runs the one command whose
    `kind` matches `job.capability`. Command output (argv, exit code, stdout,
    stderr) is stored as a blob artifact and its ref returned; `A2.60`/`A2.61`
    later wrap that ref into an `EvidenceItem`.

    Meant for `LocalWorkerBroker(capabilities=build_local_command_capabilities(...))`
    — same-host execution only. A Kubernetes-backed broker needs an
    equivalent in-container command runner, not this one.
    """

    def _run(job: WorkerJob) -> ArtifactRef:
        command = _find_command(artifacts, tenant_id=tenant_id, job=job)

        result = subprocess.run(
            command.argv,
            cwd=command.working_directory,
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
            "working_directory": command.working_directory,
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

    def _run_benchmark(job: WorkerJob) -> ArtifactRef:
        """Run a "benchmark"-kind command and extract pytest-benchmark's own rounds.

        `pytest-benchmark --benchmark-json=...` records every raw calibrated
        round it actually timed (`benchmarks[].stats.data`, in seconds), not
        just an average — those rounds become the `benchmark_rounds` list
        A2.62 turns into one `EvidenceItem` per round, real multi-sample
        evidence instead of the single exit-code sample every other command
        kind produces.
        """

        command = _find_command(artifacts, tenant_id=tenant_id, job=job)

        result = subprocess.run(
            command.argv,
            cwd=command.working_directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=job.timeout_seconds,
            check=False,
        )

        json_path = Path(command.working_directory) / BENCHMARK_JSON_FILENAME
        rounds: list[dict[str, Any]] = []
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                for bench in data.get("benchmarks", []):
                    name = bench.get("name")
                    # A fast function easily calibrates to 100k+ rounds within
                    # pytest-benchmark's `--benchmark-max-time` -- every round
                    # is a real, independently-timed sample, but turning all
                    # of them into `EvidenceItem`s downstream (A2.62) would
                    # build/serialize/hash a Pydantic object per round. Cap to
                    # a small prefix; still ample real samples for any
                    # `minimum_samples` requirement (today's are 1 or 3).
                    for value in bench.get("stats", {}).get("data", [])[:_MAX_BENCHMARK_SAMPLES]:
                        rounds.append({"name": name, "value": value, "unit": "seconds"})
            except (OSError, json.JSONDecodeError, AttributeError, TypeError):
                rounds = []
            finally:
                json_path.unlink(missing_ok=True)

        output = {
            "command_id": command.command_id,
            "argv": command.argv,
            "working_directory": command.working_directory,
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

    return {"unit": _run, "lint": _run, "type": _run, "benchmark": _run_benchmark}


__all__ = ["BENCHMARK_JSON_FILENAME", "CapabilityFn", "build_local_command_capabilities"]
