from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import NodePorts, build_b1_registrations, build_b1_runtime
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.node_runtime import NodeRuntime
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.b1 import (
    DetectionReport,
    DiscoveryScanContext,
    HistoricalSourceInventory,
    ObservedSourceIdentity,
    QualifiedOpportunity,
    ReadAuthorization,
    RegisteredSource,
    RegisteredSourceSet,
)
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import (
    IntentRecord,
    IntentStatus,
    PolicyDecision,
    PolicyRequest,
)
from production_optimizer.orchestration.catalog import B1_NODE_IDS
from production_optimizer.orchestration.subgraphs import build_b1_graph


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


class _AllowPolicy:
    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=True, decision="allow", policy_version=request.policy_version, reasons=[]
        )

    def healthcheck(self) -> bool:
        return True


_TENANT = "TENANT-B1"
_CASE_ID = "OPT-B1-1"


def _seed_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"b1-fixture\"\n"
        "dependencies = [\"pytest-benchmark>=4.0\"]\n"
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (repo / "tests" / "test_ok_benchmark.py").write_text(
        "def test_ok_benchmark(benchmark):\n    benchmark(lambda: 1 + 1)\n"
    )
    return repo


def _seed_registered_sources(
    store: _MemoryArtifactStore, sources: list[RegisteredSource]
) -> ArtifactRef:
    payload = RegisteredSourceSet(
        artifact_id="seed-registry",
        tenant_id=_TENANT,
        case_id=_CASE_ID,
        created_at=datetime.now(UTC),
        producer=ProducerIdentity(name="test", version="1.0"),
        content_digest=f"sha256:{'0' * 64}",
        parent_digests=[],
        sources=sources,
    )
    content = canonical_json(payload.model_dump(mode="json", exclude={"content_digest"}))
    digest = sha256_digest(content)
    ref = ArtifactRef(
        artifact_type="RegisteredSourceSet",
        schema_version="1.0",
        artifact_id="seed-registry",
        content_digest=digest,
        uri="memory://seed-registry",
    )
    sealed_content = canonical_json(
        {**TypeAdapter(dict[str, Any]).validate_json(content), "content_digest": digest}
    )
    store.seed_json(ref, sealed_content)
    return ref


def _state(registry_ref: ArtifactRef) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID,
        "thread_id": "THREAD-B1-1",
        "tenant_id": _TENANT,
        "entrypoint": "discovery",
        "lane": "automatic",
        "baseline_mode": "historical_recovery",
        "artifact_refs": [registry_ref],
    }


def _ports(store: _MemoryArtifactStore, *, workers: Any = None) -> NodePorts:
    return NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=workers
    )


def _advance(runtime: NodeRuntime, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    merged = dict(state)
    merged.update(result)
    merged["artifact_refs"] = [*existing_refs, *new_refs]
    return merged


def _model_from_ref[T: BaseModel](
    store: _MemoryArtifactStore, ref: ArtifactRef, model: type[T]
) -> T:
    content = store.read(tenant_id=_TENANT, ref=ref)
    data = TypeAdapter(dict[str, Any]).validate_json(content)
    data.setdefault("content_digest", ref.content_digest)
    return model.model_validate(data)


def _ref_by_type(state: dict[str, Any], artifact_type: str) -> ArtifactRef:
    for ref in cast("list[ArtifactRef]", state["artifact_refs"]):
        if ref.artifact_type == artifact_type:
            return ref
    raise AssertionError(f"no artifact ref of type {artifact_type!r} in state")


def test_b1_registrations_cover_every_native_node() -> None:
    registrations = build_b1_registrations()
    native = {node_id for node_id in B1_NODE_IDS if node_id != "B1.95"}
    assert set(registrations) == native


def test_b1_10_through_31_produce_real_artifacts(tmp_path: Path) -> None:
    repo = _seed_python_repo(tmp_path)
    store = _MemoryArtifactStore()
    registry_ref = _seed_registered_sources(
        store,
        [
            RegisteredSource(
                source_id="src-1",
                feature_id="checkout",
                repository_id="checkout-repo",
                local_path=str(repo),
            )
        ],
    )
    runtime = build_b1_runtime(ports=_ports(store))
    state = _state(registry_ref)

    state = _advance(runtime, "B1.10", state)
    scan = _model_from_ref(store, _ref_by_type(state, "DiscoveryScanContext"), DiscoveryScanContext)
    assert scan.trigger == "scheduled"
    assert scan.window_end > scan.window_start

    state = _advance(runtime, "B1.20", state)
    registry = _model_from_ref(
        store, _ref_by_type(state, "RegisteredSourceSet"), RegisteredSourceSet
    )
    assert len(registry.sources) == 1

    state = _advance(runtime, "B1.21", state)
    identity = _model_from_ref(
        store, _ref_by_type(state, "ObservedSourceIdentity"), ObservedSourceIdentity
    )
    assert identity.repository_id == "checkout-repo"
    assert identity.git_revision is None  # tmp_path repo is not a real git repo

    state = _advance(runtime, "B1.30", state)
    inventory = _model_from_ref(
        store, _ref_by_type(state, "HistoricalSourceInventory"), HistoricalSourceInventory
    )
    assert inventory.source_id == "src-1"
    assert inventory.revisions == []

    state = _advance(runtime, "B1.31", state)
    authorization = _model_from_ref(
        store, _ref_by_type(state, "ReadAuthorization"), ReadAuthorization
    )
    assert authorization.allowed is True
    assert authorization.authorized_query_kinds == ["metrics", "logs", "traces", "llm_evidence"]


def test_b1_query_branches_are_honest_and_unavailable(tmp_path: Path) -> None:
    repo = _seed_python_repo(tmp_path)
    store = _MemoryArtifactStore()
    registry_ref = _seed_registered_sources(
        store,
        [
            RegisteredSource(
                source_id="src-1",
                feature_id="checkout",
                repository_id="repo",
                local_path=str(repo),
            )
        ],
    )
    runtime = build_b1_runtime(ports=_ports(store))
    state = _state(registry_ref)
    for node_id in ("B1.10", "B1.20", "B1.21", "B1.30", "B1.31"):
        state = _advance(runtime, node_id, state)

    for node_id in ("B1.32", "B1.33", "B1.34", "B1.35"):
        state = _advance(runtime, node_id, state)
        from production_optimizer.contracts.b1 import HistoricalEvidenceBranch

        artifact_id = f"{_CASE_ID}-{node_id}-HistoricalEvidenceBranch"
        ref = next(
            r
            for r in cast("list[ArtifactRef]", state["artifact_refs"])
            if r.artifact_type == "HistoricalEvidenceBranch" and r.artifact_id == artifact_id
        )
        branch = _model_from_ref(store, ref, HistoricalEvidenceBranch)
        assert branch.evidence == []
        assert branch.unavailable_reason is not None
        assert "no" in branch.unavailable_reason and "adapter" in branch.unavailable_reason


def test_b1_full_scan_with_no_real_history_closes_honestly(tmp_path: Path) -> None:
    """BR-B1-011: a no-op scan is success, not failure -- the graph must run
    to a clean CLOSED end, not crash, when there is no real signal."""

    repo = _seed_python_repo(tmp_path)
    store = _MemoryArtifactStore()
    registry_ref = _seed_registered_sources(
        store,
        [
            RegisteredSource(
                source_id="src-1",
                feature_id="checkout",
                repository_id="repo",
                local_path=str(repo),
            )
        ],
    )
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT)
    )
    try:
        runtime = build_b1_runtime(ports=_ports(store, workers=broker))
        graph = build_b1_graph(runtime)

        result = graph.invoke(_state(registry_ref))

        # No real evidence -> zero signals -> zero scores -> B1.71 correctly
        # rejects before ever reaching B1.80/81 -- honest, not a crash.
        assert result["node_routes"]["B1.71"] == "rejected"
        assert "B1.90" not in set(result["completed_nodes"])
        assert "DetectionReport" not in {
            ref.artifact_type for ref in result["artifact_refs"]
        }
    finally:
        broker.close()


def test_b1_downstream_chain_seals_real_detection_report_and_opportunity(tmp_path: Path) -> None:
    """Bypasses the (currently always-empty) query branches by injecting a
    real `DetectionSignal` directly into `b1_signals` -- proves bind -> score
    -> qualify -> seal DetectionReport -> reconstruct request -> intake
    policy -> recover baseline (real A2) -> seal QualifiedOpportunity all
    work correctly once real evidence exists, without waiting on a real
    metrics/logs/traces adapter to prove it."""

    from production_optimizer.contracts.b1 import DetectionSignal

    repo = _seed_python_repo(tmp_path)
    store = _MemoryArtifactStore()
    registry_ref = _seed_registered_sources(
        store,
        [
            RegisteredSource(
                source_id="src-1", feature_id="checkout", repository_id="repo", local_path=str(repo)
            )
        ],
    )
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT)
    )
    try:
        ports = _ports(store, workers=broker)
        runtime = build_b1_runtime(ports=ports)
        state = _state(registry_ref)
        for node_id in ("B1.10", "B1.20", "B1.21", "B1.30", "B1.31"):
            state = _advance(runtime, node_id, state)

        injected_signal = DetectionSignal(
            signal_id="signal-real-1",
            signal_kind="regression",
            run_group_ids=["run-group-1"],
            metric_id="unit_command_result",
            description="unit test exit code regressed",
            baseline_value=0.0,
            observed_value=1.0,
            effect_size=1.0,
            unit="exit_code",
            severity=0.9,
            detected_at=datetime.now(UTC),
        )
        state = {**state, "b1_signals": [injected_signal]}

        for node_id in ("B1.60", "B1.61", "B1.62", "B1.70", "B1.71"):
            state = _advance(runtime, node_id, state)
        assert state["node_routes"]["B1.71"] == "continue"

        state = _advance(runtime, "B1.81", state)
        assert state["node_routes"]["B1.81"] == "continue"
        report = _model_from_ref(store, _ref_by_type(state, "DetectionReport"), DetectionReport)
        assert len(report.signals) == 1
        assert len(report.scores) == 1
        assert report.qualification_decisions[0].qualified is True

        state = _advance(runtime, "B1.90", state)
        assert _ref_by_type(state, "OptimizationRequest") is not None

        state = _advance(runtime, "B1.91", state)
        assert state["node_routes"]["B1.91"] == "continue"

        graph = build_b1_graph(runtime)
        # Re-run B1.95 (real A2) directly via the shared runtime's A2 nodes,
        # matching how `build_b1_graph` embeds it, without re-walking B1.10-91.
        from production_optimizer.orchestration.subgraphs import build_a2_graph

        a2_result = build_a2_graph(runtime).invoke(state)
        state = {**state, **a2_result}
        assert "A2.95" in set(a2_result.get("completed_nodes", []))

        state = _advance(runtime, "B1.96", state)
        opportunity = _model_from_ref(
            store, _ref_by_type(state, "QualifiedOpportunity"), QualifiedOpportunity
        )
        assert opportunity.detection_report_digest == report.content_digest
        del graph
    finally:
        broker.close()
