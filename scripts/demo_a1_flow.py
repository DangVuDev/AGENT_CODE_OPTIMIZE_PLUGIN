from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from production_optimizer.application import NodePorts, build_a1_runtime
from production_optimizer.contracts.a1 import ManualCasePayload
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.platform import IntentRecord, IntentStatus
from production_optimizer.orchestration.subgraphs import build_a1_graph


class MemoryArtifactStore:
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
        del tenant_id, content, content_digest, media_type
        raise NotImplementedError("A1 demo only writes JSON artifacts")

    def read(self, *, tenant_id: str, ref: ArtifactRef) -> bytes:
        del tenant_id
        return self._content_by_uri[ref.uri]

    def verify(self, *, tenant_id: str, ref: ArtifactRef) -> bool:
        del tenant_id
        content = self._content_by_uri.get(ref.uri)
        return content is not None and sha256_digest(content) == ref.content_digest

    def seed_json(self, ref: ArtifactRef, content: bytes) -> None:
        self._content_by_uri[ref.uri] = content


class MemoryIntentLedger:
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


def main() -> None:
    with TemporaryDirectory(dir=".local-data") as workspace:
        source_root = Path(workspace)
        (source_root / "pyproject.toml").write_text(
            "[project]\nname = \"demo-a1\"\n",
            encoding="utf-8",
        )
        (source_root / "tests").mkdir()
        (source_root / "tests" / "test_sample.py").write_text(
            "def test_ok():\n    assert True\n",
            encoding="utf-8",
        )

        cases = {
            "structured_owner_success": ManualCasePayload(
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
                local_path=str(source_root),
                allowed_root=str(source_root),
                actor_id="owner-1",
                actor_role="owner",
            ),
            "missing_metric_clarification": ManualCasePayload(
                raw_text="Please optimize this local repository.",
                local_path=str(source_root),
                allowed_root=str(source_root),
                actor_id="owner-1",
                actor_role="owner",
            ),
            "requester_needs_approval": ManualCasePayload(
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
                local_path=str(source_root),
                allowed_root=str(source_root),
                actor_id="requester-1",
                actor_role="requester",
            ),
        }

        for case_name, payload in cases.items():
            run_case(case_name, payload)


def run_case(case_name: str, payload: ManualCasePayload) -> None:
    store = MemoryArtifactStore()
    payload_ref = seed_payload(store, payload)
    graph = build_a1_graph(build_a1_runtime(ports=NodePorts(store, MemoryIntentLedger())))
    result = graph.invoke(
        {
            "case_id": f"OPT-DEMO-{case_name}",
            "thread_id": f"THREAD-DEMO-{case_name}",
            "tenant_id": "TENANT-DEMO",
            "entrypoint": "manual",
            "lane": "manual",
            "baseline_mode": "active_collection",
            "artifact_refs": [payload_ref],
        }
    )
    pending = result.get("pending_interrupt")
    print(f"CASE: {case_name}")
    print(f"  current_node: {result.get('current_node')}")
    print(f"  A1.80 route: {result.get('node_routes', {}).get('A1.80')}")
    print(f"  A1.90 route: {result.get('node_routes', {}).get('A1.90')}")
    print(f"  request_ref: {getattr(result.get('request_ref'), 'artifact_type', None)}")
    print(f"  pending_interrupt: {None if pending is None else pending.stage}")
    print(
        "  artifacts: "
        + ", ".join(ref.artifact_type for ref in result.get("artifact_refs", []))
    )
    print()


def seed_payload(store: MemoryArtifactStore, payload: ManualCasePayload) -> ArtifactRef:
    content = canonical_json(payload)
    ref = ArtifactRef(
        artifact_type="ManualCasePayload",
        schema_version="1.0",
        artifact_id="payload",
        content_digest=sha256_digest(content),
        uri=f"memory://payload/{sha256_digest(content)}",
    )
    store.seed_json(ref, content)
    return ref


if __name__ == "__main__":
    main()
