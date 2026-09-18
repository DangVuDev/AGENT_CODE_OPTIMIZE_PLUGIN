# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import production_optimizer.application.a1_handlers as a1_handlers
from production_optimizer.adapters.production.bootstrap_policies import build_bootstrap_policy
from production_optimizer.application import NodePorts, build_a1_runtime
from production_optimizer.application.a1_handlers.nodes import (
    a1_40_bounded_read_discovery_languages_manifests_tools_tests as a1_40_handler,
)
from production_optimizer.application.resume import resume_case
from production_optimizer.contracts.a1 import (
    A1QualityReport,
    CriterionInput,
    EvidenceRequirementSet,
    FeatureScope,
    IntakeEnvelope,
    LocalSourceIdentity,
    ManualCasePayload,
    OptimizationRequest,
    RawRequestDraft,
    WorkloadIdentity,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.evaluation import ContainerCommandSpec, EvaluationSpec
from production_optimizer.contracts.platform import (
    ActorContext,
    IntentRecord,
    IntentStatus,
    ModelCompletionRequest,
    ModelCompletionResult,
)
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


def _state(
    ref: ArtifactRef,
    *,
    actor_id: str = "requester-1",
    actor_roles: set[str] | None = None,
) -> dict[str, object]:
    return {
        "case_id": "OPT-A1-1",
        "thread_id": "THREAD-A1-1",
        "tenant_id": "TENANT-A",
        "entrypoint": "manual",
        "lane": "manual",
        "baseline_mode": "active_collection",
        "actor_context": ActorContext(
            actor_id=actor_id,
            tenant_id="TENANT-A",
            roles=actor_roles or {"requester", "owner"},
            authenticated_at=datetime.now(UTC),
        ),
        "artifact_refs": [ref],
    }


class _ScriptedModel:
    def __init__(self, responses: list[dict[str, Any] | None]) -> None:
        self.responses = responses
        self.requests: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.requests.append(request)
        response = self.responses.pop(0)
        return ModelCompletionResult(
            request_id=f"request-{len(self.requests)}",
            model_id=request.model_id,
            model_version="test-v1",
            raw_text=json.dumps(response) if response is not None else "invalid",
            parsed_json=response,
            valid_json=response is not None,
            input_tokens=20,
            output_tokens=40,
            stop_reason="stop",
        )

    def healthcheck(self) -> bool:
        return True


def _ports(
    store: _MemoryArtifactStore,
    *,
    model: _ScriptedModel | None = None,
    policy: Any = None,
) -> NodePorts:
    return NodePorts(
        artifacts=store,
        intents=_MemoryIntentLedger(),
        model=model,
        model_id="scripted-a1-model" if model else None,
        policy=policy,
    )


def _read_artifact[T: BaseModel](
    store: _MemoryArtifactStore, ref: ArtifactRef, model: type[T]
) -> T:
    content = store.read(tenant_id="TENANT-A", ref=ref)
    raw: dict[str, Any] = json.loads(content)
    raw.setdefault("content_digest", ref.content_digest)
    return model.model_validate(raw)


def _artifact_ref(result: dict[str, Any], artifact_type: str) -> ArtifactRef:
    return next(r for r in result["artifact_refs"] if r.artifact_type == artifact_type)


def test_a1_production_handlers_freeze_structured_request(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='pilot'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    artifact_types = {ref.artifact_type for ref in result["artifact_refs"]}
    assert "A1.95" in set(result["completed_nodes"])
    assert result["request_ref"].artifact_type == "OptimizationRequest"
    request = _read_artifact(store, result["request_ref"], OptimizationRequest)
    assert request.feature_scope is not None
    assert request.feature_scope.feature_id == "checkout"
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


def test_a1_freezes_docker_compose_evaluation_contract(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text(
        "services:\n  app:\n    image: local/test\n", encoding="utf-8"
    )
    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path).model_copy(
        update={
            "execution_profile": "docker_compose",
            "compose_file": "compose.yaml",
            "application_services": ["app"],
            "commands": None,
            "guardrail_metric_id": "correctness",
            "evaluations": [
                EvaluationSpec(
                    evaluation_id="checkout-feature",
                    command=ContainerCommandSpec(
                        service="app", argv=["sh", "scripts/evaluate-checkout.sh"]
                    ),
                    repetitions=3,
                    warmup_runs=1,
                    expected_metric_ids={"p95_latency_ms", "correctness"},
                )
            ],
        }
    )
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    request = _read_artifact(store, result["request_ref"], OptimizationRequest)
    assert request.execution is not None
    assert request.execution.compose_file == "compose.yaml"
    assert request.execution.evaluations[0].evaluation_id == "checkout-feature"
    # `commands` is never derived from the evaluation id (unlike the old
    # single `command_id`, which used to be filled with the evaluation_id as
    # a non-executable placeholder A2.31 had to special-case around) -- the
    # real evaluation argv lives in `execution.evaluations[].command` only.
    assert request.workload.commands is None


def test_manual_case_payload_accepts_partial_intent_for_clarification(tmp_path: Path) -> None:
    base = {
        "local_path": str(tmp_path),
        "allowed_root": str(tmp_path),
        "feature_id": "checkout",
        "metric_id": "p95_latency_ms",
        "direction": "minimize",
        "target": 180.0,
        "unit": "ms",
        "workload_id": "checkout-load",
        "environment_id": "local-dev",
        "commands": {"unit": "pytest"},
        "actor_id": "requester-1",
    }
    for omitted_field in (
        "feature_id",
        "metric_id",
        "direction",
        "target",
        "unit",
        "workload_id",
        "environment_id",
        "commands",
    ):
        assert ManualCasePayload.model_validate({**base, omitted_field: None})

    with pytest.raises(ValidationError):
        ManualCasePayload(
            local_path=str(tmp_path),
            allowed_root=str(tmp_path),
            actor_id="requester-1",
        )


def test_a1_rejects_payload_actor_that_does_not_match_host_identity(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    with pytest.raises(ValueError, match="actor_id does not match"):
        graph.invoke(
            _state(
                _payload_ref(store, payload),
                actor_id="different-actor",
                actor_roles={"owner"},
            )
        )


def test_a1_production_handlers_interrupt_for_non_approver_role(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path, actor_role="requester")
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    assert "A1.90" in set(result["completed_nodes"])
    assert "A1.95" not in set(result["completed_nodes"])
    assert result["node_routes"]["A1.90"] == "approval"
    assert result["pending_interrupt"].stage == "A1.90"


def test_a1_90_uses_versioned_policy_decision_when_configured(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    policy = build_bootstrap_policy(policy_version="approval-policy-v2")
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store, policy=policy)))

    result = graph.invoke(_state(_payload_ref(store, payload)))
    request = _read_artifact(store, result["request_ref"], OptimizationRequest)

    assert request.approval.policy_version == "approval-policy-v2"
    assert request.approval.artifact_digest == request.request_fingerprint


def test_a1_90_resumes_after_approval_via_resume_case(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path, actor_role="requester")
    ports = _ports(store)
    graph = build_a1_graph(build_a1_runtime(ports=ports))

    halted = graph.invoke(_state(_payload_ref(store, payload)))
    assert halted["node_routes"]["A1.90"] == "approval"
    interrupt = halted["pending_interrupt"]
    assert interrupt.stage == "A1.90"

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

    assert resumed["node_routes"]["A1.90"] == "continue"
    assert "A1.95" in set(resumed["completed_nodes"])
    assert resumed["request_ref"].artifact_type == "OptimizationRequest"


def _full_structured_payload(tmp_path: Path, *, actor_role: str = "owner") -> ManualCasePayload:
    return ManualCasePayload(
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        feature_id="checkout",
        metric_id="p95_latency_ms",
        direction="minimize",
        target=180.0,
        unit="ms",
        workload_id="checkout-load",
        environment_id="local-dev",
        commands={"unit": "pytest"},
        actor_id="requester-1",
        actor_role=actor_role,
    )


def test_a1_61_seals_every_declared_criterion_with_its_own_weight(tmp_path: Path) -> None:
    """BR-A1-002 allows more than one primary criterion, and S01.40 ranks by
    each one's weight-normalized benefit -- so A1.61 must seal all of them,
    keeping the requester's weights, not collapse them into a single
    `"primary"` criterion."""

    (tmp_path / "pyproject.toml").write_text("[project]\nname='pilot'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")

    store = _MemoryArtifactStore()
    payload = ManualCasePayload(
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        feature_id="checkout",
        criteria=[
            CriterionInput(
                metric_id="p95_latency_ms", direction="minimize", target=180.0, unit="ms", weight=3
            ),
            CriterionInput(
                metric_id="cost_per_request_usd",
                direction="minimize",
                target=0.02,
                unit="usd",
                weight=1,
            ),
        ],
        workload_id="checkout-load",
        environment_id="local-dev",
        commands={"unit": "pytest"},
        actor_id="requester-1",
        actor_role="owner",
    )
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    request = _read_artifact(store, result["request_ref"], OptimizationRequest)
    assert [c.criterion_id for c in request.criteria] == ["p95-latency-ms", "cost-per-request-usd"]
    assert [c.weight for c in request.criteria] == [3.0, 1.0]
    assert [c.target for c in request.criteria] == [180.0, 0.02]
    # A1.71 must bind one evidence requirement per criterion, keyed off the
    # same ids -- otherwise A1.80 rejects the request as uncovered.
    assert {r.criterion_id for r in request.evidence_requirements} >= {
        "p95-latency-ms",
        "cost-per-request-usd",
    }


def test_manual_case_payload_rejects_duplicate_criterion_metrics(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ManualCasePayload(
            local_path=str(tmp_path),
            allowed_root=str(tmp_path),
            feature_id="checkout",
            criteria=[
                CriterionInput(metric_id="latency_ms", direction="minimize", target=1, unit="ms"),
                CriterionInput(metric_id="latency_ms", direction="maximize", target=9, unit="ms"),
            ],
            actor_id="requester-1",
        )


# --- Phase 2: Use discovered project information ---------------------------


def test_a1_50_uses_discovered_vendor_paths(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='pilot'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    pass\n")
    node_modules = tmp_path / "node_modules" / "lodash"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text("module.exports = {};\n")
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "site.py").write_text("# stub\n")

    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    scope = _read_artifact(store, _artifact_ref(result, "FeatureScope"), FeatureScope)
    assert ".git/**" in scope.exclude_paths
    assert "**/node_modules/**" in scope.exclude_paths
    assert "**/.venv/**" in scope.exclude_paths


def test_a1_80_rejects_on_discovery_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='pilot'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")

    original_discover = a1_handlers._shared._discover_project_profile

    def _truncated_discover(state: object, root: Path) -> object:
        profile = original_discover(state, root)  # type: ignore[arg-type]
        return a1_handlers._seal(profile.model_copy(update={"discovery_truncated": True}))

    monkeypatch.setattr(a1_40_handler, "_discover_project_profile", _truncated_discover)

    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    assert "A1.80" in set(result["completed_nodes"])
    assert result["node_routes"]["A1.80"] == "clarification"
    report = _read_artifact(store, _artifact_ref(result, "A1QualityReport"), A1QualityReport)
    assert any("truncated" in conflict.lower() for conflict in report.conflicts)


# --- Phase 3: Language-based workload defaults ------------------------------


def test_a1_70_performance_metric_uses_repeated_measurement(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='pilot'\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('hi')\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")

    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    workload = _read_artifact(store, _artifact_ref(result, "WorkloadIdentity"), WorkloadIdentity)
    assert workload.workload is not None
    assert workload.workload.repetitions == 3
    assert workload.workload.warmup_runs == 1


def test_a1_70_respects_dominant_language(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    for i in range(7):
        (tmp_path / "src" / f"file{i}.ts").write_text("export const x = 1;\n")
    for i in range(3):
        (tmp_path / f"util{i}.py").write_text("def f():\n    pass\n")

    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    workload = _read_artifact(store, _artifact_ref(result, "WorkloadIdentity"), WorkloadIdentity)
    assert workload.workload is not None
    assert workload.workload.repetitions == 3
    assert workload.workload.warmup_runs == 1


def test_a1_70_unknown_language_still_honors_metric_protocol(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n")
    (tmp_path / "notes.txt").write_text("just notes\n")

    store = _MemoryArtifactStore()
    payload = _full_structured_payload(tmp_path)
    graph = build_a1_graph(build_a1_runtime(ports=_ports(store)))

    result = graph.invoke(_state(_payload_ref(store, payload)))

    workload = _read_artifact(store, _artifact_ref(result, "WorkloadIdentity"), WorkloadIdentity)
    assert workload.workload is not None
    assert workload.workload.repetitions == 3
    assert workload.workload.warmup_runs == 1


def test_a1_extracts_raw_request_and_freezes_metric_specific_evidence(tmp_path: Path) -> None:
    model = _ScriptedModel(
        [
            {
                "objective_statement": "Reduce checkout p95 latency to 180 ms",
                "feature_id": "checkout",
                "metric_id": "p95_latency_ms",
                "direction": "minimize",
                "target": 180.0,
                "unit": "ms",
                "guardrail_metric_id": "unit_command_result",
                "workload_id": "checkout-load",
                "dataset_id": None,
                "environment_id": "local-dev",
                "requester_hypothesis": "Database calls dominate latency",
                "confidence": 0.94,
            }
        ]
    )
    payload = ManualCasePayload(
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        raw_text=(
            "Reduce checkout p95 latency to 180 ms using checkout-load in local-dev; "
            "run pytest and preserve correctness."
        ),
        actor_id="owner-1",
        actor_role="owner",
    )
    store = _MemoryArtifactStore()
    result = build_a1_graph(build_a1_runtime(ports=_ports(store, model=model))).invoke(
        _state(_payload_ref(store, payload), actor_id="owner-1", actor_roles={"owner"})
    )

    intake = _read_artifact(store, _artifact_ref(result, "IntakeEnvelope"), IntakeEnvelope)
    draft = _read_artifact(store, _artifact_ref(result, "RawRequestDraft"), RawRequestDraft)
    requirements = _read_artifact(
        store,
        _artifact_ref(result, "EvidenceRequirementSet"),
        EvidenceRequirementSet,
    )
    primary = next(
        item for item in requirements.evidence_requirements if item.criterion_id == "primary"
    )

    assert intake.input_mode == "raw"
    assert draft.unresolved_fields == []
    assert draft.field_provenance["metric_id"] == "raw_text:model"
    assert primary.accepted_source_types == {"benchmark", "telemetry"}
    assert primary.aggregation == "p95"
    assert primary.canonical_unit == "ms"
    assert result["request_ref"].artifact_type == "OptimizationRequest"


def test_a1_mixed_input_conflict_routes_to_clarification(tmp_path: Path) -> None:
    model = _ScriptedModel(
        [
            {
                "objective_statement": "Optimize checkout latency",
                "feature_id": "checkout",
                "metric_id": "p95_latency_ms",
                "direction": "minimize",
                "target": 250.0,
                "unit": "ms",
                "workload_id": "checkout-load",
                "environment_id": "local-dev",
                "confidence": 0.9,
            }
        ]
    )
    payload = _full_structured_payload(tmp_path).model_copy(
        update={"raw_text": "Reduce checkout p95 latency to 250 ms"}
    )
    store = _MemoryArtifactStore()
    result = build_a1_graph(build_a1_runtime(ports=_ports(store, model=model))).invoke(
        _state(_payload_ref(store, payload))
    )

    draft = _read_artifact(store, _artifact_ref(result, "RawRequestDraft"), RawRequestDraft)
    assert "target: raw and structured values disagree" in draft.conflicts
    assert result["node_routes"]["A1.80"] == "clarification"
    assert "A1.90" not in set(result["completed_nodes"])


def test_a1_raw_request_without_model_fails_closed_to_clarification(tmp_path: Path) -> None:
    payload = ManualCasePayload(
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        raw_text="Please make checkout faster.",
        actor_id="owner-1",
        actor_role="owner",
    )
    store = _MemoryArtifactStore()
    result = build_a1_graph(build_a1_runtime(ports=_ports(store))).invoke(
        _state(_payload_ref(store, payload), actor_id="owner-1", actor_roles={"owner"})
    )

    report = _read_artifact(store, _artifact_ref(result, "A1QualityReport"), A1QualityReport)
    assert "metric_id" in report.missing_fields
    assert result["node_routes"]["A1.80"] == "clarification"


def test_a1_does_not_inherit_git_identity_from_parent_repository(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    result = build_a1_graph(build_a1_runtime(ports=_ports(store))).invoke(
        _state(_payload_ref(store, _full_structured_payload(tmp_path)))
    )

    source = _read_artifact(
        store, _artifact_ref(result, "LocalSourceIdentity"), LocalSourceIdentity
    )
    assert source.git_revision is None
    assert source.dirty is False
    assert source.untracked_count == 0


def test_a1_rejects_workload_that_cannot_fit_worker_budget(tmp_path: Path) -> None:
    payload = _full_structured_payload(tmp_path).model_copy(
        update={"repetitions": 5, "warmup_runs": 2, "maximum_worker_seconds": 3}
    )
    store = _MemoryArtifactStore()
    result = build_a1_graph(build_a1_runtime(ports=_ports(store))).invoke(
        _state(_payload_ref(store, payload))
    )

    report = _read_artifact(store, _artifact_ref(result, "A1QualityReport"), A1QualityReport)
    assert any("maximum_worker_seconds" in value for value in report.invalid_values)
    assert result["node_routes"]["A1.80"] == "clarification"
