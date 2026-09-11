from __future__ import annotations

from pathlib import Path

from production_optimizer.application import NodePorts, build_a1_runtime
from production_optimizer.contracts.a1 import ManualCasePayload
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.platform import IntentRecord, IntentStatus
from production_optimizer.orchestration.subgraphs import build_a1_graph


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


def _payload_ref(store: _MemoryArtifactStore, payload: ManualCasePayload) -> ArtifactRef:
    content = canonical_json(payload)
    digest = sha256_digest(content)
    ref = ArtifactRef(
        artifact_type="ManualCasePayload",
        schema_version="1.0",
        artifact_id="payload-1",
        content_digest=digest,
        uri="memory://payload-1",
    )
    store.seed_json(ref, content)
    return ref


def _state(ref: ArtifactRef) -> dict[str, object]:
    return {
        "case_id": "OPT-A1-1",
        "thread_id": "THREAD-A1-1",
        "tenant_id": "TENANT-A",
        "entrypoint": "manual",
        "lane": "manual",
        "baseline_mode": "active_collection",
        "artifact_refs": [ref],
    }


def _ports(store: _MemoryArtifactStore) -> NodePorts:
    return NodePorts(artifacts=store, intents=_MemoryIntentLedger())


def test_a1_production_handlers_freeze_structured_request(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='pilot'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    store = _MemoryArtifactStore()
    payload = ManualCasePayload(
        structured_request={
            "objective": {
                "statement": "Reduce checkout p95 latency",
                "feature_id": "checkout",
            },
            "criteria": [
                {
                    "metric_id": "p95_latency_ms",
                    "direction": "minimize",
                    "target": 180.0,
                    "unit": "ms",
                }
            ],
            "workload": {
                "workload_id": "checkout-load",
                "environment_id": "local-dev",
                "command_id": "pytest",
            },
        },
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        actor_id="requester-1",
        actor_role="owner",
    )
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    artifact_types = {ref.artifact_type for ref in result["artifact_refs"]}
    assert "A1.95" in set(result["completed_nodes"])
    assert result["request_ref"].artifact_type == "OptimizationRequest"
    assert {
        "CanonicalObjective",
        "CriterionSet",
        "GuardrailSet",
        "ExecutionBudgetArtifact",
        "WorkloadIdentity",
        "EvidenceRequirementSet",
        "A1QualityReport",
        "A1ApprovalDecision",
        "OptimizationRequest",
    } <= artifact_types
    assert result["pending_interrupt"] is None if "pending_interrupt" in result else True


def test_a1_production_handlers_interrupt_on_missing_metric(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    payload = ManualCasePayload(
        raw_text="Please optimize this local repository.",
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        actor_id="requester-1",
        actor_role="owner",
        policy_version="intake-policy-v1",
    )
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    assert "A1.80" in set(result["completed_nodes"])
    assert "A1.90" not in set(result["completed_nodes"])
    assert result["node_routes"]["A1.80"] == "clarification"
    assert result["pending_interrupt"].stage == "A1.80"


def test_a1_production_handlers_interrupt_for_non_approver_role(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    payload = ManualCasePayload(
        structured_request={
            "objective": {
                "statement": "Reduce checkout p95 latency",
                "feature_id": "checkout",
            },
            "criteria": [
                {
                    "metric_id": "p95_latency_ms",
                    "direction": "minimize",
                    "target": 180.0,
                    "unit": "ms",
                }
            ],
            "workload": {
                "workload_id": "checkout-load",
                "environment_id": "local-dev",
            },
        },
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        actor_id="requester-1",
        actor_role="requester",
    )
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    assert "A1.90" in set(result["completed_nodes"])
    assert "A1.95" not in set(result["completed_nodes"])
    assert result["node_routes"]["A1.90"] == "approval"
    assert result["pending_interrupt"].stage == "A1.90"
