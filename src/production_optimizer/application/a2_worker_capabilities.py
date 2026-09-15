from __future__ import annotations

import io
import json
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    RepositoryCommand,
    SourceSnapshot,
    VerificationManifest,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.evaluation import (
    ComposeEvaluationWorkerResult,
    EvaluationAttemptResult,
    EvaluationOutput,
)
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


def _validate_compose_config(raw_config: str) -> None:
    """Fail closed on Compose capabilities outside the evaluation sandbox."""

    try:
        config = TypeAdapter(dict[str, Any]).validate_json(raw_config)
    except (ValueError, TypeError) as exc:
        raise ValueError("docker compose config did not return valid JSON") from exc
    services = config.get("services")
    if not isinstance(services, dict) or not services:
        raise ValueError("Compose configuration must declare at least one service")
    violations: list[str] = []
    for service_name, service_value in services.items():
        if not isinstance(service_value, dict):
            violations.append(f"service {service_name!r} has an invalid configuration")
            continue
        service = cast(dict[str, Any], service_value)
        if service.get("privileged") is True:
            violations.append(f"service {service_name!r} requests privileged mode")
        for namespace in ("network_mode", "pid", "ipc"):
            if service.get(namespace) == "host":
                violations.append(f"service {service_name!r} requests host {namespace}")
        if service.get("devices"):
            violations.append(f"service {service_name!r} requests host devices")
        for volume in service.get("volumes") or []:
            source = volume.get("source") if isinstance(volume, dict) else str(volume).split(":")[0]
            if source and (str(source).startswith(("/", "\\")) or "docker.sock" in str(source)):
                violations.append(f"service {service_name!r} requests host mount {source!r}")
    volumes = config.get("volumes") or {}
    if isinstance(volumes, dict):
        for volume_name, volume_value in volumes.items():
            if isinstance(volume_value, dict) and volume_value.get("external"):
                violations.append(f"volume {volume_name!r} is external")
    if violations:
        raise ValueError("unsafe Compose configuration: " + "; ".join(violations))


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


def _find_snapshot(
    artifacts: ArtifactStore, *, tenant_id: str, job: WorkerJob
) -> SourceSnapshot | None:
    snapshot_ref = next(
        (ref for ref in job.input_refs if ref.artifact_type == "SourceSnapshot"), None
    )
    if snapshot_ref is None:
        return None
    raw = artifacts.read(tenant_id=tenant_id, ref=snapshot_ref)
    payload = TypeAdapter(dict[str, Any]).validate_json(raw)
    payload.setdefault("content_digest", snapshot_ref.content_digest)
    return SourceSnapshot.model_validate(payload)


def _find_request(
    artifacts: ArtifactStore, *, tenant_id: str, job: WorkerJob
) -> OptimizationRequest:
    request_ref = next(
        (ref for ref in job.input_refs if ref.artifact_type == "OptimizationRequest"), None
    )
    if request_ref is None:
        raise ValueError(f"worker job {job.job_id!r} has no OptimizationRequest input ref")
    raw = artifacts.read(tenant_id=tenant_id, ref=request_ref)
    payload = TypeAdapter(dict[str, Any]).validate_json(raw)
    payload.setdefault("content_digest", request_ref.content_digest)
    return OptimizationRequest.model_validate(payload)


@contextmanager
def _execution_workspace(
    artifacts: ArtifactStore, *, tenant_id: str, job: WorkerJob, fallback: str
) -> Any:
    snapshot = _find_snapshot(artifacts, tenant_id=tenant_id, job=job)
    if snapshot is None or snapshot.archive_ref is None:
        yield Path(fallback)
        return
    archive_bytes = artifacts.read(tenant_id=tenant_id, ref=snapshot.archive_ref)
    if sha256_digest(archive_bytes) != snapshot.archive_ref.content_digest:
        raise ValueError("source archive failed digest verification")
    with tempfile.TemporaryDirectory(prefix="optimizer-a2-worker-") as directory:
        root = Path(directory).resolve()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for member in archive.infolist():
                destination = (root / member.filename).resolve()
                if not destination.is_relative_to(root):
                    raise ValueError("source archive contains a path traversal entry")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        yield root


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
        with _execution_workspace(
            artifacts, tenant_id=tenant_id, job=job, fallback=command.working_directory
        ) as workspace:
            result = subprocess.run(
                command.argv,
                cwd=workspace,
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
            "working_directory": "immutable-source-snapshot",
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

        rounds: list[dict[str, Any]] = []
        with _execution_workspace(
            artifacts, tenant_id=tenant_id, job=job, fallback=command.working_directory
        ) as workspace:
            result = subprocess.run(
                command.argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=job.timeout_seconds,
                check=False,
            )
            json_path = workspace / BENCHMARK_JSON_FILENAME
            if json_path.exists():
                try:
                    data = json.loads(json_path.read_text(encoding="utf-8"))
                    for bench in data.get("benchmarks", []):
                        name = bench.get("name")
                        values = bench.get("stats", {}).get("data", [])
                        for value in values[:_MAX_BENCHMARK_SAMPLES]:
                            rounds.append({"name": name, "value": value, "unit": "seconds"})
                except (OSError, json.JSONDecodeError, AttributeError, TypeError):
                    rounds = []

        output = {
            "command_id": command.command_id,
            "argv": command.argv,
            "working_directory": "immutable-source-snapshot",
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

    def _run_compose_evaluation(job: WorkerJob) -> ArtifactRef:
        """Run the sealed evaluator protocol in an isolated Compose project."""

        request = _find_request(artifacts, tenant_id=tenant_id, job=job)
        execution = request.execution
        if execution is None:
            raise ValueError("compose_evaluation requires OptimizationRequest.execution")
        project_name = "optimizer-" + sha256_digest(job.job_id.encode())[7:19]
        attempts: list[EvaluationAttemptResult] = []
        lifecycle_error: str | None = None
        cleanup_confirmed = False

        with _execution_workspace(
            artifacts, tenant_id=tenant_id, job=job, fallback="."
        ) as workspace:
            compose_path = (workspace / execution.compose_file).resolve()
            if not compose_path.is_relative_to(workspace.resolve()) or not compose_path.is_file():
                raise ValueError("compose_file is outside the snapshot or does not exist")
            base = [
                "docker",
                "compose",
                "-p",
                project_name,
                "-f",
                str(compose_path),
            ]

            def invoke(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    argv,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )

            try:
                for phase, argv, timeout in (
                    (
                        "config",
                        [*base, "config", "--format", "json"],
                        execution.startup_timeout_seconds,
                    ),
                    ("build", [*base, "build"], execution.startup_timeout_seconds),
                    (
                        "up",
                        [*base, "up", "-d", "--wait", *execution.application_services],
                        execution.startup_timeout_seconds,
                    ),
                ):
                    result = invoke(argv, timeout)
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"compose {phase} failed ({result.returncode}): "
                            f"{result.stderr[-_OUTPUT_TAIL_BYTES:]}"
                        )
                    if phase == "config":
                        _validate_compose_config(result.stdout)

                for evaluation in execution.evaluations:
                    total_runs = evaluation.warmup_runs + evaluation.repetitions
                    for run_index in range(total_runs):
                        command = [*base, "exec", "-T"]
                        if evaluation.command.working_directory:
                            command.extend(["--workdir", evaluation.command.working_directory])
                        command.extend([evaluation.command.service, *evaluation.command.argv])
                        result = invoke(command, evaluation.command.timeout_seconds)
                        if run_index < evaluation.warmup_runs:
                            if result.returncode != 0:
                                raise RuntimeError(
                                    f"warmup for {evaluation.evaluation_id!r} failed: "
                                    f"{result.stderr[-_OUTPUT_TAIL_BYTES:]}"
                                )
                            continue
                        output: EvaluationOutput | None = None
                        error: str | None = None
                        try:
                            output = EvaluationOutput.model_validate_json(result.stdout)
                            missing = evaluation.expected_metric_ids - set(output.metrics)
                            if missing:
                                raise ValueError(
                                    "missing expected metrics: " + ", ".join(sorted(missing))
                                )
                            if output.feature_id != request.objective.feature_id:
                                raise ValueError("evaluator feature_id does not match request")
                        except (ValueError, TypeError) as exc:
                            error = str(exc)
                        attempts.append(
                            EvaluationAttemptResult(
                                evaluation_id=evaluation.evaluation_id,
                                repetition=run_index - evaluation.warmup_runs + 1,
                                exit_code=result.returncode,
                                output=output if result.returncode == 0 and error is None else None,
                                stdout=result.stdout[-_OUTPUT_TAIL_BYTES:],
                                stderr=result.stderr[-_OUTPUT_TAIL_BYTES:],
                                error=error or (
                                    f"evaluator exited with {result.returncode}"
                                    if result.returncode != 0
                                    else None
                                ),
                            )
                        )
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                lifecycle_error = str(exc)
            finally:
                try:
                    cleanup = invoke(
                        [*base, "down", "--volumes", "--remove-orphans"],
                        execution.cleanup_timeout_seconds,
                    )
                    cleanup_confirmed = cleanup.returncode == 0
                    if not cleanup_confirmed and lifecycle_error is None:
                        lifecycle_error = cleanup.stderr[-_OUTPUT_TAIL_BYTES:]
                except (OSError, subprocess.TimeoutExpired) as exc:
                    if lifecycle_error is None:
                        lifecycle_error = f"compose cleanup failed: {exc}"

        worker_result = ComposeEvaluationWorkerResult(
            project_name=project_name,
            attempts=attempts,
            cleanup_confirmed=cleanup_confirmed,
            lifecycle_error=lifecycle_error,
        )
        content = canonical_json(worker_result.model_dump(mode="json"))
        return artifacts.put_blob(
            tenant_id=tenant_id,
            content=content,
            content_digest=sha256_digest(content),
            media_type="application/json",
        )

    return {
        "unit": _run,
        "lint": _run,
        "type": _run,
        "benchmark": _run_benchmark,
        "compose_evaluation": _run_compose_evaluation,
    }


__all__ = ["BENCHMARK_JSON_FILENAME", "CapabilityFn", "build_local_command_capabilities"]
