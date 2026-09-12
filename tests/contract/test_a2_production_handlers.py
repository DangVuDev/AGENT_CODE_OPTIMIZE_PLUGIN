from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, TypeAdapter

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import A2_BLOCKED_NODES, NodePorts, build_a2_runtime
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.node_runtime import NodeNotEnabledError, NodeRuntime
from production_optimizer.application.resume import resume_case
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.a2 import (
    A2IntakeDecision,
    BaselineSnapshot,
    BranchEvidenceRefs,
    CollectorPlan,
    ComparabilityReport,
    EnvironmentManifest,
    EvidenceBundle,
    EvidenceQualityReport,
    ExecutionAuthorization,
    NormalizedEvidenceSet,
    RawEvidenceFanIn,
    RepositoryManifest,
    SourceSnapshot,
    VerificationManifest,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import (
    ActorContext,
    IntentRecord,
    IntentStatus,
    ModelCompletionRequest,
    ModelCompletionResult,
    PolicyDecision,
    PolicyRequest,
    WorkerJob,
    WorkerReceipt,
)
from production_optimizer.orchestration.catalog import A2_NODE_IDS
from production_optimizer.orchestration.subgraphs import build_a2_graph

_ZERO_DIGEST = "sha256:" + "0" * 64
_TEST_PRODUCER = ProducerIdentity(name="test", version="1.0")


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self._content_by_uri: dict[str, bytes] = {}

    def put_json(
        self, *, tenant_id: str, content: bytes, content_digest: str, idempotency_key: str
    ) -> ArtifactRef:
        del tenant_id
        uri = f"memory://{idempotency_key}"
        self._content_by_uri[uri] = content
        return ArtifactRef(
            artifact_type="JsonArtifact",
            schema_version="1.0",
            artifact_id=idempotency_key,
            content_digest=content_digest,
            uri=uri,
        )

    def put_blob(
        self, *, tenant_id: str, content: bytes, content_digest: str, media_type: str
    ) -> ArtifactRef:
        del tenant_id, media_type
        uri = f"memory://blob/{content_digest}"
        self._content_by_uri[uri] = content
        return ArtifactRef(
            artifact_type="Blob",
            schema_version="1.0",
            artifact_id=content_digest,
            content_digest=content_digest,
            uri=uri,
        )

    def read(self, *, tenant_id: str, ref: ArtifactRef) -> bytes:
        del tenant_id
        return self._content_by_uri[ref.uri]

    def verify(self, *, tenant_id: str, ref: ArtifactRef) -> bool:
        del tenant_id
        content = self._content_by_uri.get(ref.uri)
        return content is not None and sha256_digest(content) == ref.content_digest

    def seed_json(self, ref: ArtifactRef, content: bytes) -> None:
        self._content_by_uri[ref.uri] = content


class _MemoryIntentLedger:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IntentRecord] = {}

    def prepare(self, intent: IntentRecord) -> IntentRecord:
        key = (intent.tenant_id, intent.idempotency_key)
        existing = self._records.get(key)
        if existing is not None and existing.status is IntentStatus.COMPLETED:
            return existing
        self._records[key] = intent
        return intent

    def get(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord | None:
        return self._records.get((tenant_id, idempotency_key))

    def complete(
        self, *, tenant_id: str, idempotency_key: str, output_ref: ArtifactRef
    ) -> IntentRecord:
        record = self._records[(tenant_id, idempotency_key)]
        updated = record.model_copy(
            update={"status": IntentStatus.COMPLETED, "output_ref": output_ref}
        )
        self._records[(tenant_id, idempotency_key)] = updated
        return updated

    def mark_unknown(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord:
        record = self._records[(tenant_id, idempotency_key)]
        updated = record.model_copy(update={"status": IntentStatus.UNKNOWN})
        self._records[(tenant_id, idempotency_key)] = updated
        return updated


def _build_request(
    *,
    allowed_root_id: str,
    relative_path: str,
    minimum_samples: int = 3,
    accepted_source_types: frozenset[str] = frozenset({"benchmark", "test", "telemetry"}),
) -> OptimizationRequest:
    return OptimizationRequest(
        artifact_id="request-1",
        tenant_id="TENANT-A",
        case_id="OPT-A2-1",
        created_at=datetime.now(UTC),
        producer=_TEST_PRODUCER,
        origin=Origin.MANUAL,
        scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="repo-123",
            allowed_root_id=allowed_root_id,
            relative_path=relative_path,
            requested_revision=None,
        ),
        objective=Objective(statement="improve latency", feature_id="local-feature"),
        criteria=[
            Criterion(
                criterion_id="primary",
                metric_id="p95_latency_ms",
                direction="minimize",
                target=100.0,
                unit="ms",
                weight=1.0,
            )
        ],
        guardrails=[],
        workload=WorkloadContract(
            workload_id="local-workload",
            environment_id="local-env",
            repetitions=3,
            warmup_runs=1,
            concurrency=1,
            cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="evidence-primary",
                criterion_id="primary",
                accepted_source_types=set(accepted_source_types),
                minimum_samples=minimum_samples,
                mandatory=True,
            )
        ],
        budget=ExecutionBudget(
            deadline_seconds=300,
            maximum_worker_seconds=180,
            maximum_model_tokens=4000,
            maximum_storage_bytes=50_000_000,
        ),
        approval=ApprovalBinding(
            approval_id="approval-1",
            actor_id="actor-1",
            actor_role="owner",
            decision="approve",
            artifact_digest=_ZERO_DIGEST,
            policy_version="test-v1",
        ),
        request_fingerprint=_ZERO_DIGEST,
        content_digest=_ZERO_DIGEST,
    )


def _seed_request(
    store: _MemoryArtifactStore,
    *,
    allowed_root_id: str,
    relative_path: str,
    minimum_samples: int = 3,
    accepted_source_types: frozenset[str] = frozenset({"benchmark", "test", "telemetry"}),
) -> ArtifactRef:
    request = _build_request(
        allowed_root_id=allowed_root_id,
        relative_path=relative_path,
        minimum_samples=minimum_samples,
        accepted_source_types=accepted_source_types,
    )
    content = canonical_json(request)
    digest = sha256_digest(content)
    ref = ArtifactRef(
        artifact_type="OptimizationRequest",
        schema_version="1.0",
        artifact_id="request-1",
        content_digest=digest,
        uri="memory://request-1",
    )
    store.seed_json(ref, content)
    return ref


def _state(ref: ArtifactRef) -> dict[str, Any]:
    return {
        "case_id": "OPT-A2-1",
        "thread_id": "THREAD-A2-1",
        "tenant_id": "TENANT-A",
        "entrypoint": "manual",
        "lane": "manual",
        "baseline_mode": "active_collection",
        "request_ref": ref,
        "artifact_refs": [ref],
    }


def _ports(store: _MemoryArtifactStore) -> NodePorts:
    return NodePorts(artifacts=store, intents=_MemoryIntentLedger())


class _AllowPolicy:
    def __init__(self, *, denied_kinds: frozenset[str] = frozenset()) -> None:
        self._denied_kinds = denied_kinds

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        kind = request.facts.get("kind") or request.facts.get("source_type")
        allowed = kind not in self._denied_kinds
        return PolicyDecision(
            allowed=allowed,
            decision="allow" if allowed else "deny",
            policy_version=request.policy_version,
            reasons=[] if allowed else [f"{kind} is denied by test policy"],
        )

    def healthcheck(self) -> bool:
        return True


class _AcceptingWorkerBroker:
    def __init__(self) -> None:
        self.submitted: list[WorkerJob] = []

    def submit(self, job: WorkerJob) -> WorkerReceipt:
        self.submitted.append(job)
        return WorkerReceipt(job_id=job.job_id, accepted=True)

    def cancel(self, *, job_id: str, reason: str) -> bool:
        del job_id, reason
        return False

    def reconcile(self, *, job_id: str, idempotency_key: str) -> ArtifactRef | None:
        del job_id, idempotency_key
        return None


def _ports_with_execution(
    store: _MemoryArtifactStore,
    *,
    policy: _AllowPolicy | None = None,
    workers: _AcceptingWorkerBroker | None = None,
) -> NodePorts:
    return NodePorts(
        artifacts=store,
        intents=_MemoryIntentLedger(),
        policy=policy or _AllowPolicy(),
        workers=workers or _AcceptingWorkerBroker(),
    )


def _advance(
    runtime: NodeRuntime, node_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    return {**state, **result, "artifact_refs": [*existing_refs, *new_refs]}


def _model_from_ref[T: BaseModel](
    store: _MemoryArtifactStore, ref: ArtifactRef, model: type[T]
) -> T:
    content = store.read(tenant_id="TENANT-A", ref=ref)
    data = TypeAdapter(dict[str, Any]).validate_json(content)
    data.setdefault("content_digest", ref.content_digest)
    return model.model_validate(data)


def _ref_by_type(state: dict[str, Any], artifact_type: str) -> ArtifactRef:
    refs = cast("list[ArtifactRef]", state["artifact_refs"])
    for ref in refs:
        if ref.artifact_type == artifact_type:
            return ref
    raise AssertionError(f"no artifact ref of type {artifact_type!r} in state")


def test_a2_10_verifies_request_approval(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    request_ref = _seed_request(store, allowed_root_id=str(tmp_path), relative_path=".")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    result = runtime.execute("A2.10", state)  # type: ignore[arg-type]

    assert result["current_node"] == "A2.10"
    assert len(result["artifact_refs"]) > 0
    intake_ref = result["artifact_refs"][0]
    assert intake_ref.artifact_type == "A2IntakeDecision"

    decision = _model_from_ref(store, intake_ref, A2IntakeDecision)
    assert decision.verified
    assert not decision.mismatches
    assert decision.source_reachable


def test_a2_10_flags_unreachable_source(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    missing_root = tmp_path / "does-not-exist"
    request_ref = _seed_request(store, allowed_root_id=str(missing_root), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    result = runtime.execute("A2.10", state)  # type: ignore[arg-type]

    intake_ref = result["artifact_refs"][0]
    decision = _model_from_ref(store, intake_ref, A2IntakeDecision)
    assert not decision.verified
    assert not decision.source_reachable


def test_a2_20_captures_snapshot(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Test Repo")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hello')")

    request_ref = _seed_request(
        store, allowed_root_id=str(repo.parent), relative_path="repo"
    )
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    result = runtime.execute("A2.20", state)  # type: ignore[arg-type]

    assert result["current_node"] == "A2.20"
    assert len(result["artifact_refs"]) > 0
    snapshot_ref = result["artifact_refs"][0]
    assert snapshot_ref.artifact_type == "SourceSnapshot"

    snapshot = _model_from_ref(store, snapshot_ref, SourceSnapshot)
    assert snapshot.dirty is False
    assert len(snapshot.files) == 2
    assert any(f.relative_path == "README.md" for f in snapshot.files)
    assert any(f.relative_path == "src/main.py" for f in snapshot.files)


def test_a2_20_raises_for_missing_source(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    missing_root = tmp_path / "does-not-exist"
    request_ref = _seed_request(store, allowed_root_id=str(missing_root), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    try:
        runtime.execute("A2.20", state)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing source path")


def _seed_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
        "[tool.ruff]\n"
        'line-length = 100\n'
        "[tool.mypy]\n"
        "strict = true\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hello')")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n")
    return repo


def test_a2_30_parses_repository_manifest(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)

    manifest_ref = _ref_by_type(state, "RepositoryManifest")
    manifest = _model_from_ref(store, manifest_ref, RepositoryManifest)
    assert manifest.languages.get("python", 0) > 0
    assert "pyproject.toml" in manifest.manifest_files
    assert any(root.endswith("tests") for root in manifest.test_roots)
    assert manifest.tool_coverage["pytest"] == 1.0
    assert manifest.tool_coverage["ruff"] == 1.0
    assert manifest.tool_coverage["mypy"] == 1.0
    assert manifest.commands == []


def test_a2_31_resolves_repository_owned_commands(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)

    verification_ref = _ref_by_type(state, "VerificationManifest")
    verification = _model_from_ref(store, verification_ref, VerificationManifest)
    kinds = {c.kind for c in verification.commands}
    assert kinds == {"unit", "lint", "type"}
    assert verification.rejected_commands == ["benchmark: no pytest-benchmark dependency detected"]


def test_a2_31_rejects_unconfigured_tools(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# empty repo")
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)

    verification_ref = _ref_by_type(state, "VerificationManifest")
    verification = _model_from_ref(store, verification_ref, VerificationManifest)
    assert verification.commands == []
    assert len(verification.rejected_commands) == 5


def _act_available() -> bool:
    import shutil

    if shutil.which("act") is None:
        return False
    probe = subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=False)
    return probe.returncode == 0


def test_a2_31_detects_command_from_ci_config(tmp_path: Path) -> None:
    if not _act_available():
        pytest.skip("act and/or a reachable Docker daemon are not available")
    store = _MemoryArtifactStore()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo with CI but no pyproject.toml convention")
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  run-tests:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo running the real project CI job\n"
    )
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)

    verification_ref = _ref_by_type(state, "VerificationManifest")
    verification = _model_from_ref(store, verification_ref, VerificationManifest)
    unit_command = next(c for c in verification.commands if c.kind == "unit")
    assert unit_command.source == "ci_config"
    assert unit_command.argv[0] == "act"
    assert "run-tests" in unit_command.argv


def test_a2_31_ci_detection_runs_the_real_job_via_act(tmp_path: Path) -> None:
    """The `act`-produced command isn't just detected -- it actually runs
    the real CI job through the normal A2.60/61/62 worker pipeline."""

    if not _act_available():
        pytest.skip("act and/or a reachable Docker daemon are not available")
    store = _MemoryArtifactStore()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo with CI but no pyproject.toml convention")
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  run-tests:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo running the real project CI job\n"
    )
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        ports = NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
        runtime = build_a2_runtime(ports=ports)
        state = _state(request_ref)
        state = _advance(runtime, "A2.20", state)
        state = _advance(runtime, "A2.30", state)
        state = _advance(runtime, "A2.31", state)
        state = _advance_to_a2_50(runtime, state)

        state = _advance(runtime, "A2.61", state)
        test_branch = _model_from_ref(
            store, _require_branch_ref_for_test(state, "A2.61"), BranchEvidenceRefs
        )
        assert test_branch.unavailable_reason is None
        assert test_branch.evidence[0].evidence_type == "unit_command_result"
        assert test_branch.evidence[0].value == 0.0
    finally:
        broker.close()


class _ScriptedA2ModelProvider:
    """Fake `ModelProviderPort` for A2.31's LLM-fallback command proposal.

    Always proposes the same safe, always-succeeding command
    (`python -c "print(1)"`) rather than a real test runner, so a test using
    it can exercise the full authorize -> execute -> evidence pipeline
    without depending on a build tool actually being installed.
    """

    def __init__(self) -> None:
        self.calls: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.calls.append(request)
        payload = {"argv": ["python", "-c", "print(1)"], "kind": "unit"}
        return ModelCompletionResult(
            request_id=f"req-{len(self.calls)}",
            model_id=request.model_id,
            model_version="scripted-1",
            raw_text=json.dumps(payload),
            parsed_json=payload,
            valid_json=True,
            input_tokens=10,
            output_tokens=5,
            stop_reason="end_turn",
        )

    def healthcheck(self) -> bool:
        return True


def test_a2_31_halts_for_approval_then_resumes_with_llm_command(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# no detectable convention at all")
    request_ref = _seed_request(
        store, allowed_root_id=str(repo.parent), relative_path="repo", minimum_samples=1
    )
    model = _ScriptedA2ModelProvider()
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        ports = NodePorts(
            artifacts=store,
            intents=_MemoryIntentLedger(),
            policy=_AllowPolicy(),
            workers=broker,
            model=model,
        )
        graph = build_a2_graph(build_a2_runtime(ports=ports))

        halted = graph.invoke(_state(request_ref))
        assert halted["node_routes"]["A2.31"] == "approval"
        interrupt = halted["pending_interrupt"]
        assert interrupt.stage == "A2.31"
        assert len(model.calls) == 1

        now = datetime.now(UTC)
        command = ResumeInterruptCommand(
            command_id="resume-1",
            tenant_id="TENANT-A",
            case_id=interrupt.case_id,
            thread_id=interrupt.thread_id,
            interrupt_id=interrupt.interrupt_id,
            actor_id="owner-1",
            actor_roles={"owner"},
            decision="approve",
            artifact_digest=interrupt.artifact_digest,
            policy_version=interrupt.policy_version,
            issued_at=now,
        )
        actor = ActorContext(
            actor_id="owner-1", tenant_id="TENANT-A", roles={"owner"}, authenticated_at=now
        )

        resumed = resume_case(graph=graph, state=halted, command=command, actor=actor, now=now)

        verification_ref = _ref_by_type(resumed, "VerificationManifest")
        verification = _model_from_ref(store, verification_ref, VerificationManifest)
        assert len(verification.commands) == 1
        assert verification.commands[0].source == "llm_suggested"
        assert verification.commands[0].argv == ["python", "-c", "print(1)"]
        assert len(model.calls) == 1
        assert "A2.95" in set(resumed["completed_nodes"])
        assert resumed["baseline_ref"].artifact_type == "BaselineSnapshot"
    finally:
        broker.close()


def test_a2_40_binds_collector_plan(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    request_ref = _seed_request(store, allowed_root_id=str(tmp_path), relative_path=".")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    result = runtime.execute("A2.40", state)  # type: ignore[arg-type]
    plan_ref = result["artifact_refs"][0]
    plan = _model_from_ref(store, plan_ref, CollectorPlan)

    assert not plan.unresolved_requirements
    assert len(plan.bindings) == 1
    assert plan.bindings[0].requirement_id == "evidence-primary"
    assert plan.bindings[0].collector_id in {
        "local-pytest-runner",
        "local-benchmark-runner",
        "otel-local-collector",
    }


def test_a2_41_captures_environment_manifest(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    request_ref = _seed_request(store, allowed_root_id=str(tmp_path), relative_path=".")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    result = runtime.execute("A2.41", state)  # type: ignore[arg-type]
    manifest_ref = result["artifact_refs"][0]
    manifest = _model_from_ref(store, manifest_ref, EnvironmentManifest)

    assert manifest.tool_versions.get("python")
    assert manifest.concurrency >= 1
    assert manifest.cache_state == "warm"


def test_a2_50_authorizes_and_submits_worker_jobs(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    workers = _AcceptingWorkerBroker()
    runtime = build_a2_runtime(ports=_ports_with_execution(store, workers=workers))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)
    state = _advance(runtime, "A2.40", state)
    state = _advance(runtime, "A2.41", state)
    state = _advance(runtime, "A2.50", state)

    authorization_ref = _ref_by_type(state, "ExecutionAuthorization")
    authorization = _model_from_ref(store, authorization_ref, ExecutionAuthorization)

    assert authorization.authorized
    assert not authorization.denied_capabilities
    assert len(authorization.worker_job_ids) == 3  # unit, lint, type commands
    assert len(workers.submitted) == 3
    assert {job.capability for job in workers.submitted} == {"unit", "lint", "type"}


def test_a2_50_denies_disallowed_command_kind(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    workers = _AcceptingWorkerBroker()
    policy = _AllowPolicy(denied_kinds=frozenset({"lint"}))
    runtime = build_a2_runtime(ports=_ports_with_execution(store, policy=policy, workers=workers))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)
    state = _advance(runtime, "A2.40", state)
    state = _advance(runtime, "A2.41", state)
    state = _advance(runtime, "A2.50", state)

    authorization_ref = _ref_by_type(state, "ExecutionAuthorization")
    authorization = _model_from_ref(store, authorization_ref, ExecutionAuthorization)

    assert not authorization.authorized
    assert any("lint" in reason for reason in authorization.denied_capabilities)
    assert len(authorization.worker_job_ids) == 2
    assert {job.capability for job in workers.submitted} == {"unit", "type"}


def test_a2_50_requires_policy_and_worker_ports(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)
    state = _advance(runtime, "A2.40", state)
    state = _advance(runtime, "A2.41", state)

    try:
        runtime.execute("A2.50", state)  # type: ignore[arg-type]
    except RuntimeError as exc:
        assert "PolicyPort" in str(exc)
        assert "WorkerBroker" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when policy/workers ports are missing")


def _advance_to_a2_50(runtime: NodeRuntime, state: dict[str, Any]) -> dict[str, Any]:
    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)
    state = _advance(runtime, "A2.40", state)
    state = _advance(runtime, "A2.41", state)
    return _advance(runtime, "A2.50", state)


def test_a2_60_and_a2_61_collect_real_command_evidence(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        ports = NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_50(runtime, state)

        state = _advance(runtime, "A2.60", state)
        static_branch = _model_from_ref(
            store, _require_branch_ref_for_test(state, "A2.60"), BranchEvidenceRefs
        )
        assert static_branch.branch_kind == "static"
        assert static_branch.unavailable_reason is None
        assert {item.evidence_type for item in static_branch.evidence} == {
            "lint_command_result",
            "type_command_result",
        }
        assert all(isinstance(item.value, float) for item in static_branch.evidence)

        state = _advance(runtime, "A2.61", state)
        test_branch = _model_from_ref(
            store, _require_branch_ref_for_test(state, "A2.61"), BranchEvidenceRefs
        )
        assert test_branch.branch_kind == "test"
        assert test_branch.unavailable_reason is None
        assert len(test_branch.evidence) == 1
        assert test_branch.evidence[0].evidence_type == "unit_command_result"
        assert test_branch.evidence[0].value == 0.0  # the seeded test suite passes
    finally:
        broker.close()


def test_a2_62_reports_unavailable_without_benchmark_command(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports_with_execution(store))
    state = _advance_to_a2_50(runtime, state)

    state = _advance(runtime, "A2.62", state)
    branch = _model_from_ref(
        store, _require_branch_ref_for_test(state, "A2.62"), BranchEvidenceRefs
    )
    assert branch.branch_kind == "metric"
    assert not branch.evidence
    assert branch.unavailable_reason is not None
    assert "benchmark" in branch.unavailable_reason


def _seed_python_repo_with_benchmark(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\n"
        'name = "bench-demo"\n'
        'dependencies = ["pytest-benchmark>=4.0"]\n'
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
    )
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_bench.py").write_text(
        "import sys\n"
        'sys.path.insert(0, "src")\n'
        "from main import add\n\n"
        "def test_add_benchmark(benchmark):\n"
        "    result = benchmark(add, 2, 3)\n"
        "    assert result == 5\n"
    )
    return repo


def _pytest_benchmark_available_on_path() -> bool:
    """Check the *subprocess-resolved* `python` -- the interpreter A2.31's
    `RepositoryCommand.argv` (`["python", "-m", "pytest", ...]`) actually
    invokes, which is not necessarily this test process's own interpreter
    (`.venv`'s), matching how A2.62's real command execution resolves it."""

    probe = subprocess.run(
        ["python", "-c", "import pytest_benchmark"],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return probe.returncode == 0


def test_a2_62_collects_real_benchmark_evidence(tmp_path: Path) -> None:
    if not _pytest_benchmark_available_on_path():
        pytest.skip("pytest-benchmark not importable via the 'python' resolved on PATH")
    store = _MemoryArtifactStore()
    repo = _seed_python_repo_with_benchmark(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        ports = NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_50(runtime, state)

        state = _advance(runtime, "A2.62", state)
        branch = _model_from_ref(
            store, _require_branch_ref_for_test(state, "A2.62"), BranchEvidenceRefs
        )
        assert branch.branch_kind == "metric"
        assert branch.unavailable_reason is None
        assert len(branch.evidence) >= 3
        assert all(item.evidence_type == "benchmark_command_result" for item in branch.evidence)
        assert all(item.unit == "seconds" for item in branch.evidence)
        assert all(isinstance(item.value, float) and item.value >= 0 for item in branch.evidence)
        assert not (repo / ".optimizer-benchmark-result.json").exists()
    finally:
        broker.close()


def test_a2_63_reports_unavailable_without_telemetry_adapter(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports_with_execution(store))
    state = _advance_to_a2_50(runtime, state)

    state = _advance(runtime, "A2.63", state)
    branch = _model_from_ref(
        store, _require_branch_ref_for_test(state, "A2.63"), BranchEvidenceRefs
    )
    assert branch.branch_kind == "telemetry"
    assert not branch.evidence
    assert branch.unavailable_reason is not None


def test_a2_64_maps_python_source_and_flags_other_languages(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports_with_execution(store))
    state = _advance_to_a2_50(runtime, state)

    state = _advance(runtime, "A2.64", state)
    branch = _model_from_ref(
        store, _require_branch_ref_for_test(state, "A2.64"), BranchEvidenceRefs
    )
    assert branch.branch_kind == "source_map"
    assert branch.unavailable_reason is None
    assert len(branch.evidence) == 1
    assert branch.evidence[0].evidence_type == "source_map"
    assert 0.0 < branch.coverage["resolved"] < 1.0  # pyproject.toml is not Python


def test_a2_70_fans_in_all_five_branches(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        ports = NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_50(runtime, state)

        for node_id in ("A2.60", "A2.61", "A2.62", "A2.63", "A2.64"):
            state = _advance(runtime, node_id, state)

        state = _advance(runtime, "A2.70", state)
        fan_in_ref = _ref_by_type(state, "RawEvidenceFanIn")
        fan_in = _model_from_ref(store, fan_in_ref, RawEvidenceFanIn)

        assert set(fan_in.branch_status) == {"A2.60", "A2.61", "A2.62", "A2.63", "A2.64"}
        assert fan_in.branch_status["A2.60"] == "collected"
        assert fan_in.branch_status["A2.61"] == "collected"
        assert fan_in.branch_status["A2.62"] == "unavailable"
        assert fan_in.branch_status["A2.63"] == "unavailable"
        assert fan_in.branch_status["A2.64"] == "collected"
        assert len(fan_in.evidence_ids) == 4  # 2 static + 1 test + 1 source-map
    finally:
        broker.close()


@contextmanager
def _local_worker_ports(store: _MemoryArtifactStore) -> Any:
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        yield NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
    finally:
        broker.close()


def _advance_to_a2_70(runtime: NodeRuntime, state: dict[str, Any]) -> dict[str, Any]:
    state = _advance_to_a2_50(runtime, state)
    for node_id in ("A2.60", "A2.61", "A2.62", "A2.63", "A2.64", "A2.70"):
        state = _advance(runtime, node_id, state)
    return state


def test_a2_71_normalizes_fanned_in_evidence(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)

    with _local_worker_ports(store) as ports:
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_70(runtime, state)

        fan_in = _model_from_ref(store, _ref_by_type(state, "RawEvidenceFanIn"), RawEvidenceFanIn)
        state = _advance(runtime, "A2.71", state)
        normalized = _model_from_ref(
            store, _ref_by_type(state, "NormalizedEvidenceSet"), NormalizedEvidenceSet
        )

        assert not normalized.conversion_failures
        assert len(normalized.evidence) == len(fan_in.evidence_ids)
        assert all(item.normalized_ref == item.raw_ref for item in normalized.evidence)


def test_a2_80_binds_evidence_bundle(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)

    with _local_worker_ports(store) as ports:
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_70(runtime, state)
        state = _advance(runtime, "A2.71", state)

        snapshot = _model_from_ref(store, _ref_by_type(state, "SourceSnapshot"), SourceSnapshot)
        state = _advance(runtime, "A2.80", state)
        bundle = _model_from_ref(store, _ref_by_type(state, "EvidenceBundle"), EvidenceBundle)

        assert bundle.baseline_digest == snapshot.content_digest
        assert bundle.evidence
        assert "a2-local-worker-broker" in bundle.collector_versions
        assert "a2-ast-source-mapper" in bundle.collector_versions
        assert bundle.coverage["test"] == 1.0
        assert bundle.coverage["static"] == 2.0


def test_a2_90_fails_quality_gate_when_minimum_samples_unmet(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(
        store,
        allowed_root_id=str(repo.parent),
        relative_path="repo",
        minimum_samples=3,
        accepted_source_types=frozenset({"test"}),
    )
    state = _state(request_ref)

    with _local_worker_ports(store) as ports:
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_70(runtime, state)
        state = _advance(runtime, "A2.71", state)
        state = _advance(runtime, "A2.80", state)
        state = _advance(runtime, "A2.90", state)

        report = _model_from_ref(
            store, _ref_by_type(state, "EvidenceQualityReport"), EvidenceQualityReport
        )
        assert report.passed is False
        assert report.mandatory_coverage["evidence-primary"] is False
        assert any("evidence-primary" in failure for failure in report.sample_failures)
        assert not report.integrity_failures
        assert not report.redaction_failures


def test_a2_91_reports_comparable_dimensions(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)

    with _local_worker_ports(store) as ports:
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_70(runtime, state)
        state = _advance(runtime, "A2.71", state)
        state = _advance(runtime, "A2.80", state)
        state = _advance(runtime, "A2.91", state)

        report = _model_from_ref(
            store, _ref_by_type(state, "ComparabilityReport"), ComparabilityReport
        )
        assert report.comparable is True
        assert {d.dimension for d in report.dimensions} == {"cache_state", "workload_identity"}
        assert all(d.comparable for d in report.dimensions)


def test_a2_95_raises_when_quality_gate_did_not_pass(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)

    with _local_worker_ports(store) as ports:
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_70(runtime, state)
        state = _advance(runtime, "A2.71", state)
        state = _advance(runtime, "A2.80", state)
        state = _advance(runtime, "A2.90", state)
        state = _advance(runtime, "A2.91", state)

        try:
            runtime.execute("A2.95", state)  # type: ignore[arg-type]
        except ValueError as exc:
            assert "quality" in str(exc).lower()
        else:
            raise AssertionError("expected ValueError when quality gate did not pass")


def test_a2_95_publishes_baseline_when_gates_pass(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(
        store,
        allowed_root_id=str(repo.parent),
        relative_path="repo",
        minimum_samples=1,
        accepted_source_types=frozenset({"test"}),
    )
    state = _state(request_ref)

    with _local_worker_ports(store) as ports:
        runtime = build_a2_runtime(ports=ports)
        state = _advance_to_a2_70(runtime, state)
        state = _advance(runtime, "A2.71", state)
        state = _advance(runtime, "A2.80", state)
        state = _advance(runtime, "A2.90", state)
        state = _advance(runtime, "A2.91", state)

        quality = _model_from_ref(
            store, _ref_by_type(state, "EvidenceQualityReport"), EvidenceQualityReport
        )
        assert quality.passed is True

        state = _advance(runtime, "A2.95", state)
        baseline_ref = _ref_by_type(state, "BaselineSnapshot")
        baseline = _model_from_ref(store, baseline_ref, BaselineSnapshot)

        assert baseline.aggregates
        assert state["baseline_ref"] == baseline_ref
        unit_aggregate = next(
            a for a in baseline.aggregates if a.metric_id == "unit_command_result"
        )
        assert unit_aggregate.count == 1
        assert unit_aggregate.unit == "exit_code"


def test_a2_production_graph_publishes_baseline_on_success(tmp_path: Path) -> None:
    """End-to-end through the compiled `build_a2_graph`, not per-node `execute()`.

    Exercises real LangGraph routing (`add_routed_edge`/`add_fan_out` in
    `orchestration/subgraphs/a2.py`) and the parallel A2.60-A2.64 fan-out
    merge — the scenario the per-node tests above never cover, and the one
    that caught both the `artifact_id` collision (fixed via
    `_branch_envelope`) and the missing A2.90/A2.91 route wiring.
    """

    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(
        store,
        allowed_root_id=str(repo.parent),
        relative_path="repo",
        minimum_samples=1,
        accepted_source_types=frozenset({"test"}),
    )
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        ports = NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
        graph = build_a2_graph(build_a2_runtime(ports=ports))

        result = graph.invoke(_state(request_ref))

        completed = set(result["completed_nodes"])
        assert "A2.95" in completed
        assert result["node_routes"]["A2.90"] == "continue"
        assert result["node_routes"]["A2.91"] == "continue"
        assert result["baseline_ref"].artifact_type == "BaselineSnapshot"
    finally:
        broker.close()


def test_a2_production_graph_stops_at_quality_gate_on_failure(tmp_path: Path) -> None:
    """The routing bug this test guards: A2.90 failing must halt at `END`.

    Before `_A2_ROUTE_OVERRIDES` existed, `_a2_90` could only ever return
    `route=CONTINUE` (its `NodeSpec.allowed_routes` was `{"continue"}` for
    every A2 node), so the compiled graph would have proceeded straight to
    A2.95 even on a failed quality gate, which would then hard-crash with an
    unhandled `ValueError` instead of ending cleanly at `END`.
    """

    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id="TENANT-A")
    )
    try:
        ports = NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
        graph = build_a2_graph(build_a2_runtime(ports=ports))

        result = graph.invoke(_state(request_ref))

        completed = set(result["completed_nodes"])
        assert "A2.90" in completed
        assert "A2.95" not in completed
        assert result["node_routes"]["A2.90"] in {"missing", "rejected"}
        assert result.get("baseline_ref") is None
    finally:
        broker.close()


def _require_branch_ref_for_test(state: dict[str, Any], node_id: str) -> ArtifactRef:
    case_id = "OPT-A2-1"
    artifact_id = f"{case_id}-{node_id}-BranchEvidenceRefs"
    refs = cast("list[ArtifactRef]", state["artifact_refs"])
    for ref in refs:
        if ref.artifact_type == "BranchEvidenceRefs" and ref.artifact_id == artifact_id:
            return ref
    raise AssertionError(f"no BranchEvidenceRefs found for {node_id}")


def test_a2_blocked_nodes_stay_unregistered() -> None:
    store = _MemoryArtifactStore()
    runtime = build_a2_runtime(ports=_ports(store))

    registered = runtime.enabled_node_ids
    for node_id in A2_NODE_IDS:
        if node_id in A2_BLOCKED_NODES:
            assert node_id not in registered, f"{node_id} should stay unregistered"
        else:
            assert node_id in registered, f"{node_id} should be registered"

    blocked_ref = _seed_request(store, allowed_root_id=".", relative_path=".")
    blocked_state = _state(blocked_ref)
    for node_id in A2_BLOCKED_NODES:
        try:
            runtime.execute(node_id, blocked_state)  # type: ignore[arg-type]
        except NodeNotEnabledError:
            pass
        else:
            raise AssertionError(f"expected NodeNotEnabledError for blocked node {node_id}")
