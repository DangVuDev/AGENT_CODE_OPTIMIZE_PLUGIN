from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from pydantic import BaseModel, TypeAdapter

import production_optimizer.application.a2_worker_capabilities as worker_capabilities
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
    DecodedEvidenceObservation,
    EnvironmentManifest,
    EvidenceBundle,
    EvidenceQualityReport,
    ExecutionAuthorization,
    NormalizedEvidenceSet,
    RawEvidenceFanIn,
    RepositoryManifest,
    SourceAcquisitionRequest,
    SourceMaterialization,
    SourceSnapshot,
    VerificationManifest,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.evaluation import (
    ComposeEvaluationWorkerResult,
    ComposeExecutionContract,
    ContainerCommandSpec,
    EvaluationSpec,
)
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
from production_optimizer.contracts.registries import RegistryKind, RegistryRecord
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
    metric_id: str = "p95_latency_ms",
    canonical_unit: str = "ms",
    aggregation: Literal["mean", "p50", "p95", "p99", "sum", "rate", "verdict"] = "p95",
    source_kind: str = "local_directory",
    locator: str | None = None,
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
            source_kind=source_kind,
            locator=locator,
        ),
        objective=Objective(statement="improve latency", feature_id="local-feature"),
        criteria=[
            Criterion(
                criterion_id="primary",
                metric_id=metric_id,
                direction="minimize",
                target=100.0,
                unit=canonical_unit,
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
                metric_id=metric_id,
                canonical_unit=canonical_unit,
                aggregation=aggregation,
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
    metric_id: str = "p95_latency_ms",
    canonical_unit: str = "ms",
    aggregation: Literal["mean", "p50", "p95", "p99", "sum", "rate", "verdict"] = "p95",
    source_kind: str = "local_directory",
    locator: str | None = None,
) -> ArtifactRef:
    request = _build_request(
        allowed_root_id=allowed_root_id,
        relative_path=relative_path,
        minimum_samples=minimum_samples,
        accepted_source_types=accepted_source_types,
        metric_id=metric_id,
        canonical_unit=canonical_unit,
        aggregation=aggregation,
        source_kind=source_kind,
        locator=locator,
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


class _MemoryRegistry:
    def __init__(self, records: list[RegistryRecord]) -> None:
        self._records = records

    def resolve(
        self,
        *,
        tenant_id: str,
        registry_kind: RegistryKind,
        record_id: str,
        at: datetime,
    ) -> RegistryRecord | None:
        return next(
            (
                record
                for record in self.list_active(
                    tenant_id=tenant_id, registry_kind=registry_kind, at=at
                )
                if record.record_id == record_id
            ),
            None,
        )

    def list_active(
        self, *, tenant_id: str, registry_kind: RegistryKind, at: datetime
    ) -> list[RegistryRecord]:
        return [
            record
            for record in self._records
            if record.tenant_id == tenant_id
            and record.registry_kind == registry_kind
            and record.enabled
            and record.valid_from <= at
            and (record.valid_until is None or at < record.valid_until)
        ]


class _MaterializedSourceProvider:
    def __init__(self, materialized_path: Path) -> None:
        self.materialized_path = materialized_path
        self.requests: list[SourceAcquisitionRequest] = []

    def acquire(self, request: SourceAcquisitionRequest) -> SourceMaterialization:
        self.requests.append(request)
        return SourceMaterialization(
            source_kind=request.source_kind,
            locator=request.locator,
            local_path=str(self.materialized_path),
            resolved_revision="object-version-7",
            metadata={"provider": "test-object-store"},
        )

    def healthcheck(self) -> bool:
        return True


class _PipeScoreDecoder:
    def decode(
        self,
        *,
        decoder_id: str,
        content: bytes,
        output_schema: str,
        value_selector: str | None,
    ) -> list[DecodedEvidenceObservation]:
        assert decoder_id == "pipe-score/v1"
        assert output_schema == "campaign-value-observation/v1"
        assert value_selector is None
        return [
            DecodedEvidenceObservation(
                value=float(token), unit="points", dimensions={"scenario": "base"}
            )
            for token in content.decode("ascii").split("|")
        ]

    def healthcheck(self) -> bool:
        return True


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


def test_compose_worker_runs_lifecycle_and_validates_evaluator_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "compose.yaml").write_text("services:\n  app:\n    image: local/test\n")
    store = _MemoryArtifactStore()
    execution = ComposeExecutionContract(
        compose_file="compose.yaml",
        application_services=["app"],
        evaluations=[
            EvaluationSpec(
                evaluation_id="checkout-feature",
                command=ContainerCommandSpec(
                    service="app", argv=["sh", "scripts/evaluate-checkout.sh"]
                ),
                repetitions=2,
                warmup_runs=1,
                expected_metric_ids={"p95_latency_ms"},
            )
        ],
    )
    request = _build_request(allowed_root_id=str(tmp_path), relative_path=".").model_copy(
        update={"execution": execution}
    )
    request_content = canonical_json(request)
    request_ref = ArtifactRef(
        artifact_type="OptimizationRequest",
        schema_version="1.0",
        artifact_id="compose-request",
        content_digest=sha256_digest(request_content),
        uri="memory://compose-request",
    )
    store.seed_json(request_ref, request_content)

    @contextmanager
    def workspace(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        yield tmp_path

    commands: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(argv)
        stdout = ""
        if "config" in argv:
            stdout = json.dumps({"services": {"app": {"image": "local/test"}}})
        if "exec" in argv:
            stdout = json.dumps(
                {
                    "schema_version": "1.0",
                    "feature_id": "local-feature",
                    "metrics": {"p95_latency_ms": 143.2},
                }
            )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(worker_capabilities, "_execution_workspace", workspace)
    monkeypatch.setattr(worker_capabilities.subprocess, "run", fake_run)
    capability = build_local_command_capabilities(store, tenant_id="TENANT-A")[
        "compose_evaluation"
    ]

    result_ref = capability(
        WorkerJob(
            job_id="compose-job",
            case_id="OPT-A2-1",
            node_id="A2.50",
            idempotency_key="compose-job",
            input_refs=[request_ref],
            capability="compose_evaluation",
            timeout_seconds=180,
        )
    )

    result = ComposeEvaluationWorkerResult.model_validate_json(
        store.read(tenant_id="TENANT-A", ref=result_ref)
    )
    assert result.cleanup_confirmed
    assert len(result.attempts) == 2
    assert all(item.output is not None for item in result.attempts)
    assert sum("exec" in command for command in commands) == 3
    assert any("down" in command for command in commands)


def _advance(
    runtime: NodeRuntime, node_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    if node_id == "A2.20" and not any(
        ref.artifact_type == "A2IntakeDecision"
        for ref in cast("list[ArtifactRef]", state.get("artifact_refs", []))
    ):
        state = _advance(runtime, "A2.10", state)
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

    result = _advance(runtime, "A2.20", state)

    assert result["current_node"] == "A2.20"
    assert len(result["artifact_refs"]) > 0
    snapshot_ref = _ref_by_type(result, "SourceSnapshot")
    assert snapshot_ref.artifact_type == "SourceSnapshot"

    snapshot = _model_from_ref(store, snapshot_ref, SourceSnapshot)
    assert snapshot.dirty is False
    assert len(snapshot.files) == 2
    assert any(f.relative_path == "README.md" for f in snapshot.files)
    assert any(f.relative_path == "src/main.py" for f in snapshot.files)


def test_a2_10_rejects_missing_source_before_snapshot(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    missing_root = tmp_path / "does-not-exist"
    request_ref = _seed_request(store, allowed_root_id=str(missing_root), relative_path="repo")
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    result = runtime.execute("A2.10", state)  # type: ignore[arg-type]

    assert result["node_routes"]["A2.10"] == "rejected"
    decision = _model_from_ref(store, result["artifact_refs"][0], A2IntakeDecision)
    assert not decision.verified
    assert not decision.source_reachable


def test_a2_20_acquires_non_local_source_through_provider(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    materialized = tmp_path / "materialized-object"
    materialized.mkdir()
    (materialized / "payload.bin").write_bytes(b"domain-specific-payload")
    provider = _MaterializedSourceProvider(materialized)
    request_ref = _seed_request(
        store,
        allowed_root_id="provider-managed",
        relative_path=".",
        source_kind="object_store",
        locator="s3://tenant-bucket/input/object-version-7",
    )
    state = _state(request_ref)
    runtime = build_a2_runtime(
        ports=NodePorts(
            artifacts=store,
            intents=_MemoryIntentLedger(),
            sources=provider,
        )
    )

    state = _advance(runtime, "A2.20", state)

    snapshot = _model_from_ref(store, _ref_by_type(state, "SourceSnapshot"), SourceSnapshot)
    assert snapshot.source_kind == "object_store"
    assert snapshot.source_locator == "s3://tenant-bucket/input/object-version-7"
    assert snapshot.git_revision == "object-version-7"
    assert snapshot.archive_ref is not None
    assert store.verify(tenant_id="TENANT-A", ref=snapshot.archive_ref)
    assert provider.requests[0].repository_id == "repo-123"


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
    # "build" has no Python pyproject.toml convention at all (unlike
    # unit/lint/type/benchmark) -- it is only ever available via a declared
    # `WorkloadContract.commands["build"]`, which this Python fixture doesn't
    # set, so it is always rejected here alongside "benchmark".
    assert verification.rejected_commands == [
        "build: no build command declared",
        "benchmark: no pytest-benchmark dependency detected, and none declared",
    ]


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
    # unit, build, lint, type, benchmark all rejected (no tool_coverage
    # convention detected and none declared), plus the LLM fallback itself
    # rejecting since no ModelProviderPort is wired into this test's ports.
    assert len(verification.rejected_commands) == 6


def test_a2_31_prefers_the_requester_declared_commands(tmp_path: Path) -> None:
    """`ManualCasePayload.commands` (via A1's `WorkloadContract.commands`)
    is the highest-trust signal -- A2.31 must use a declared kind verbatim
    rather than guess from pyproject.toml conventions for that same kind,
    and it must never require the `act`/CI-replay job-detection this test
    replaces (removed: `act -l`'s job selection had no way to know which job
    actually ran the tests -- see contracts/a2.py's `RepositoryCommand.source`
    docstring). Also proves a repository this module's own Python-only
    `tool_coverage` detection can't recognize (e.g. Go) can still get a real
    `build` check purely by declaring one -- no pyproject.toml convention
    exists for `build` at all."""

    store = _MemoryArtifactStore()
    repo = tmp_path / "repo"
    repo.mkdir()
    # Also has a real pyproject.toml pytest convention, to prove the
    # declared "unit" command wins over -- not just alongside -- that
    # heuristic.
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")

    base_request = _build_request(allowed_root_id=str(repo.parent), relative_path="repo")
    request = base_request.model_copy(
        update={
            "workload": base_request.workload.model_copy(
                update={"commands": {"unit": "pytest tests/ -v", "build": "go build ./..."}}
            )
        }
    )
    request_content = canonical_json(request)
    request_ref = ArtifactRef(
        artifact_type="OptimizationRequest",
        schema_version="1.0",
        artifact_id="declared-command-request",
        content_digest=sha256_digest(request_content),
        uri="memory://declared-command-request",
    )
    store.seed_json(request_ref, request_content)
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)

    verification_ref = _ref_by_type(state, "VerificationManifest")
    verification = _model_from_ref(store, verification_ref, VerificationManifest)
    unit_commands = [c for c in verification.commands if c.kind == "unit"]
    assert len(unit_commands) == 1, "declared 'unit' must not duplicate with pyproject.toml"
    assert unit_commands[0].source == "user_declared"
    assert unit_commands[0].argv == ["pytest", "tests/", "-v"]

    build_commands = [c for c in verification.commands if c.kind == "build"]
    assert len(build_commands) == 1
    assert build_commands[0].source == "user_declared"
    assert build_commands[0].argv == ["go", "build", "./..."]


def test_a2_31_still_honors_declared_commands_under_docker_compose(tmp_path: Path) -> None:
    """Regression test for a real crash: `workload.commands` and
    `execution.evaluations[].command` are two independent channels (host-side
    build/lint/type/unit checks vs. the in-container evaluation A2.50
    dispatches), but `_detect_repository_commands` used to force `declared`
    empty whenever `request.execution is not None` -- a leftover from the old
    single `command_id`, which really was unusable under `docker_compose`
    (A1 filled it with a non-executable `evaluation_id` placeholder). A real
    `commands` declaration has no such problem and must still be honored, or
    a Go repository run with `--execution-profile docker_compose` gets an
    empty `RepositoryManifest.commands` -- which then crashes S04.90's
    `VerificationReport(check_results=...)` seal (`min_length=1`)."""

    store = _MemoryArtifactStore()
    repo = tmp_path / "repo"
    repo.mkdir()
    base_request = _build_request(allowed_root_id=str(repo.parent), relative_path="repo")
    request = base_request.model_copy(
        update={
            "workload": base_request.workload.model_copy(
                update={"commands": {"unit": "go test ./...", "build": "go build ./..."}}
            ),
            "execution": ComposeExecutionContract(
                compose_file="compose.yaml",
                application_services=["app"],
                evaluations=[
                    EvaluationSpec(
                        evaluation_id="checkout-http",
                        command=ContainerCommandSpec(
                            service="app", argv=["sh", "scripts/evaluate-checkout.sh"]
                        ),
                        repetitions=3,
                        warmup_runs=1,
                        expected_metric_ids={"p95_latency_ms"},
                    )
                ],
            ),
        }
    )
    request_content = canonical_json(request)
    request_ref = ArtifactRef(
        artifact_type="OptimizationRequest",
        schema_version="1.0",
        artifact_id="declared-command-compose-request",
        content_digest=sha256_digest(request_content),
        uri="memory://declared-command-compose-request",
    )
    store.seed_json(request_ref, request_content)
    state = _state(request_ref)
    runtime = build_a2_runtime(ports=_ports(store))

    state = _advance(runtime, "A2.20", state)
    state = _advance(runtime, "A2.30", state)
    state = _advance(runtime, "A2.31", state)

    verification_ref = _ref_by_type(state, "VerificationManifest")
    verification = _model_from_ref(store, verification_ref, VerificationManifest)
    kinds = {c.kind for c in verification.commands}
    assert kinds == {"unit", "build"}, "docker_compose must not discard declared commands"


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
        # The approved command proves only a unit-command result. It cannot
        # satisfy the p95 latency requirement, so the graph must stop at the
        # evidence-quality gate instead of relabelling it as latency data.
        assert "A2.90" in set(resumed["completed_nodes"])
        assert "A2.95" not in set(resumed["completed_nodes"])
        assert resumed["node_routes"]["A2.90"] == "missing"
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
        assert all(item.normalized_ref is not None for item in normalized.evidence)
        assert all(item.normalized_ref != item.raw_ref for item in normalized.evidence)
        assert all(
            store.verify(tenant_id="TENANT-A", ref=item.normalized_ref)
            for item in normalized.evidence
            if item.normalized_ref is not None
        )


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


def test_a2_90_accepts_a_single_deterministic_verdict(tmp_path: Path) -> None:
    """A command exit code is one verdict, not a sample from a distribution.

    `minimum_samples=3` still stands for sampled evidence (see the benchmark
    case below); demanding three identical re-runs of the same suite against
    the same immutable snapshot added no information and made every
    correctness-only case unsatisfiable.
    """

    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(
        store,
        allowed_root_id=str(repo.parent),
        relative_path="repo",
        minimum_samples=3,
        accepted_source_types=frozenset({"test"}),
        metric_id="unit_command_result",
        canonical_unit="exit_code",
        aggregation="verdict",
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
        assert report.mandatory_coverage["evidence-primary"] is True
        assert not any("evidence-primary" in failure for failure in report.sample_failures)


def test_a2_90_still_fails_when_sampled_evidence_is_missing(tmp_path: Path) -> None:
    """The gate must keep biting: a criterion that demands sampled
    (benchmark) evidence is not satisfied by a repo that produces none."""

    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    request_ref = _seed_request(
        store,
        allowed_root_id=str(repo.parent),
        relative_path="repo",
        minimum_samples=3,
        accepted_source_types=frozenset({"benchmark"}),
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
        assert {d.dimension for d in report.dimensions} == {
            "cache_state",
            "concurrency",
            "dataset_identity",
            "source_snapshot",
            "workload_identity",
        }
        assert all(d.comparable for d in report.dimensions)


def test_a2_95_raises_when_quality_gate_did_not_pass(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path)
    # Demand sampled (benchmark) evidence this repo cannot produce -- a
    # deterministic command verdict alone now legitimately satisfies its own
    # requirement, so that is no longer a way to force a failed gate.
    request_ref = _seed_request(
        store,
        allowed_root_id=str(repo.parent),
        relative_path="repo",
        accepted_source_types=frozenset({"benchmark"}),
    )
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
        metric_id="unit_command_result",
        canonical_unit="exit_code",
        aggregation="verdict",
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
        metric_id="unit_command_result",
        canonical_unit="exit_code",
        aggregation="verdict",
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
    request_ref = _seed_request(
        store,
        allowed_root_id=str(repo.parent),
        relative_path="repo",
        accepted_source_types=frozenset({"benchmark"}),
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
        assert "A2.90" in completed
        assert "A2.95" not in completed
        assert result["node_routes"]["A2.90"] in {"missing", "rejected"}
        assert result.get("baseline_ref") is None
    finally:
        broker.close()


def test_a2_executes_registered_arbitrary_evidence_recipe_end_to_end(
    tmp_path: Path,
) -> None:
    """A2 core accepts a domain value, not a Python/test/log-specific shape."""

    store = _MemoryArtifactStore()
    source = tmp_path / "domain-input"
    source.mkdir()
    (source / "campaign.asset").write_bytes(b"opaque-domain-input")
    request_ref = _seed_request(
        store,
        allowed_root_id=str(source.parent),
        relative_path=source.name,
        minimum_samples=3,
        accepted_source_types=frozenset({"business-evaluation"}),
        metric_id="campaign_value_score",
        canonical_unit="points",
        aggregation="mean",
    )
    now = datetime.now(UTC)
    registry = _MemoryRegistry(
        [
            RegistryRecord(
                registry_kind=RegistryKind.COLLECTOR,
                record_id="campaign-value-recipe",
                version=7,
                tenant_id="TENANT-A",
                valid_from=now,
                payload={
                    "collector_id": "campaign-evaluator",
                    "supported_metric_ids": ["campaign_value_score"],
                    "source_type": "business-evaluation",
                    "recipe_id": "campaign-value",
                    "recipe_version": "7.2.0",
                    "execution_node": "A2.60",
                    "executor_capability": "evaluate.campaign-value",
                    "decoder_id": "pipe-score/v1",
                    "output_schema": "campaign-value-observation/v1",
                },
            )
        ]
    )

    def evaluate_campaign_value(job: WorkerJob) -> ArtifactRef:
        del job
        content = b"41|42|43"
        return store.put_blob(
            tenant_id="TENANT-A",
            content=content,
            content_digest=sha256_digest(content),
            media_type="application/octet-stream",
        )

    broker = LocalWorkerBroker(
        capabilities={"evaluate.campaign-value": evaluate_campaign_value}
    )
    try:
        ports = NodePorts(
            artifacts=store,
            intents=_MemoryIntentLedger(),
            policy=_AllowPolicy(),
            workers=broker,
            registry=registry,
            evidence_decoders=_PipeScoreDecoder(),
        )
        result = build_a2_graph(build_a2_runtime(ports=ports)).invoke(_state(request_ref))

        assert "A2.95" in set(result["completed_nodes"])
        plan = _model_from_ref(store, _ref_by_type(result, "CollectorPlan"), CollectorPlan)
        assert plan.registry_record_versions == {"campaign-value": 7}
        bundle = _model_from_ref(store, _ref_by_type(result, "EvidenceBundle"), EvidenceBundle)
        observations = [
            item for item in bundle.evidence if item.metric_id == "campaign_value_score"
        ]
        assert [item.value for item in observations] == [41.0, 42.0, 43.0]
        assert all(item.requirement_id == "evidence-primary" for item in observations)
        assert all(item.identity.recipe_version == "7.2.0" for item in observations)
        baseline = _model_from_ref(
            store, _ref_by_type(result, "BaselineSnapshot"), BaselineSnapshot
        )
        aggregate = next(
            item for item in baseline.aggregates if item.metric_id == "campaign_value_score"
        )
        assert aggregate.mean == 42.0
        assert aggregate.unit == "points"
        assert aggregate.count == 3
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
