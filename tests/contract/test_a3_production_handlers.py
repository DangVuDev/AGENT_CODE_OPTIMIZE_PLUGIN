from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import NodePorts, build_a2_runtime, build_a3_runtime
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.node_runtime import NodeRuntime
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Guardrail,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.a3 import (
    AnalyzerObservationBranch,
    FindingSet,
    ProblemSignalSet,
    RevisionDirective,
    SolutionPortfolio,
    SolutionStrategySet,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import (
    IntentRecord,
    IntentStatus,
    ModelCompletionRequest,
    ModelCompletionResult,
    PolicyDecision,
    PolicyRequest,
)
from production_optimizer.orchestration.catalog import A3_NODE_IDS
from production_optimizer.orchestration.subgraphs import build_a3_graph

_ZERO_DIGEST = "sha256:" + "0" * 64
_TEST_PRODUCER = ProducerIdentity(name="test", version="1.0")
_TENANT_ID = "TENANT-A"
_CASE_ID = "OPT-A3-1"


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


def _build_request(
    *, allowed_root_id: str, relative_path: str, guardrails: list[Guardrail] | None = None
) -> OptimizationRequest:
    return OptimizationRequest(
        artifact_id="request-1",
        tenant_id=_TENANT_ID,
        case_id=_CASE_ID,
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
        objective=Objective(statement="fix failing tests", feature_id="local-feature"),
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
        guardrails=guardrails or [],
        workload=WorkloadContract(
            workload_id="local-workload",
            environment_id="local-env",
            repetitions=1,
            warmup_runs=0,
            concurrency=1,
            cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="evidence-primary",
                criterion_id="primary",
                accepted_source_types={"test"},
                minimum_samples=1,
                mandatory=True,
            )
        ],
        budget=ExecutionBudget(
            deadline_seconds=300,
            maximum_worker_seconds=180,
            maximum_model_tokens=100_000,
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
    guardrails: list[Guardrail] | None = None,
) -> ArtifactRef:
    request = _build_request(
        allowed_root_id=allowed_root_id, relative_path=relative_path, guardrails=guardrails
    )
    content = canonical_json(request)
    ref = ArtifactRef(
        artifact_type="OptimizationRequest",
        schema_version="1.0",
        artifact_id="request-1",
        content_digest=sha256_digest(content),
        uri="memory://request-1",
    )
    store.seed_json(ref, content)
    return ref


def _seed_python_repo(tmp_path: Path, *, failing_test: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        "[tool.ruff]\nline-length = 100\n"
        "[tool.mypy]\nstrict = true\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    (repo / "tests").mkdir()
    assertion = "assert False" if failing_test else "assert True"
    (repo / "tests" / "test_main.py").write_text(f"def test_ok():\n    {assertion}\n")
    return repo


def _state(ref: ArtifactRef) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID,
        "thread_id": "THREAD-A3-1",
        "tenant_id": _TENANT_ID,
        "entrypoint": "manual",
        "lane": "manual",
        "baseline_mode": "active_collection",
        "request_ref": ref,
        "artifact_refs": [ref],
    }


def _advance(runtime: NodeRuntime, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    merged = {**state, **result, "artifact_refs": [*existing_refs, *new_refs]}
    merged["a3_revision_attempts"] = state.get("a3_revision_attempts", 0) + result.get(
        "a3_revision_attempts", 0
    )
    merged["a3_model_tokens_spent"] = state.get("a3_model_tokens_spent", 0) + result.get(
        "a3_model_tokens_spent", 0
    )
    return merged


def _model_from_ref[T: BaseModel](
    store: _MemoryArtifactStore, ref: ArtifactRef, model: type[T]
) -> T:
    content = store.read(tenant_id=_TENANT_ID, ref=ref)
    data = TypeAdapter(dict[str, Any]).validate_json(content)
    data.setdefault("content_digest", ref.content_digest)
    return model.model_validate(data)


def _ref_by_type(state: dict[str, Any], artifact_type: str) -> ArtifactRef:
    refs = cast("list[ArtifactRef]", state["artifact_refs"])
    for ref in refs:
        if ref.artifact_type == artifact_type:
            return ref
    raise AssertionError(f"no artifact ref of type {artifact_type!r} in state")


def _stage_ref(state: dict[str, Any], node_id: str, artifact_type: str) -> ArtifactRef:
    artifact_id = f"{_CASE_ID}-{node_id}-{artifact_type}"
    refs = cast("list[ArtifactRef]", state["artifact_refs"])
    for ref in refs:
        if ref.artifact_type == artifact_type and ref.artifact_id == artifact_id:
            return ref
    raise AssertionError(f"no {artifact_type} produced by {node_id} in state")


@contextmanager
def _local_worker_ports(store: _MemoryArtifactStore) -> Any:
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
    )
    try:
        yield NodePorts(
            artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), workers=broker
        )
    finally:
        broker.close()


def _run_a2_pipeline(
    store: _MemoryArtifactStore, ports: NodePorts, state: dict[str, Any]
) -> dict[str, Any]:
    a2_runtime = build_a2_runtime(ports=ports)
    for node_id in (
        "A2.10",
        "A2.20",
        "A2.30",
        "A2.31",
        "A2.40",
        "A2.41",
        "A2.50",
        "A2.60",
        "A2.61",
        "A2.62",
        "A2.63",
        "A2.64",
        "A2.70",
        "A2.71",
        "A2.80",
        "A2.90",
        "A2.91",
        "A2.95",
    ):
        state = _advance(a2_runtime, node_id, state)
    return state


_EVIDENCE_ID_LINE = re.compile(r"Available evidence IDs \(cite only these\): (.+)")
_FINDING_LINE = re.compile(r"^- (finding-\S+)")


class _ScriptedModelProvider:
    """Fake `ModelProviderPort` grounded in the real context it is given.

    Extracts real evidence/finding IDs out of the prompt text (never
    fabricates its own) so A3.41's citation resolution and A3.51's finding
    assembly operate on genuine references, exactly like a real model would
    have to.
    """

    def __init__(self, *, bad_sequence_on_first_strategy_pass: bool = False) -> None:
        self.calls: list[ModelCompletionRequest] = []
        self._bad_sequence_first_pass = bad_sequence_on_first_strategy_pass

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.calls.append(request)
        # Use the full message list, not just the last one: a repair retry
        # appends a "please retry" instruction as the final message, but the
        # original evidence-bearing context is still further back.
        context = "\n".join(message.content for message in request.messages)
        if ":A3.40:" in request.idempotency_key:
            payload = self._finding_payload(context)
        elif ":A3.50:" in request.idempotency_key:
            payload = self._judge_payload(context)
        elif ":A3.60:" in request.idempotency_key:
            payload = self._strategy_payload(request, context)
        else:
            payload = None

        return ModelCompletionResult(
            request_id=f"req-{len(self.calls)}",
            model_id=request.model_id,
            model_version="scripted-1",
            raw_text=json.dumps(payload) if payload is not None else "",
            parsed_json=payload,
            valid_json=payload is not None,
            input_tokens=100,
            output_tokens=50,
            stop_reason="end_turn" if payload is not None else "refusal",
        )

    def healthcheck(self) -> bool:
        return True

    def _finding_payload(self, context: str) -> dict[str, Any]:
        match = _EVIDENCE_ID_LINE.search(context)
        evidence_ids = [
            e.strip() for e in (match.group(1).split(",") if match else []) if e.strip()
        ]
        # Cite the unit-test collector's evidence specifically (it is the one
        # with a nonzero value in this scenario): a real model reasoning about
        # "the unit test fails" would pick the unit-test evidence, not just
        # the first ID alphabetically.
        unit_evidence = [e for e in evidence_ids if ":A2.61:" in e]
        chosen = unit_evidence[:1] or evidence_ids[:1]
        return {
            "findings": [
                {
                    "finding_id": "finding-unit-failure",
                    "problem_signal_ids": [
                        line.split()[1]
                        for line in context.splitlines()
                        if line.startswith("- signal-")
                    ][:1]
                    or ["signal-unknown"],
                    "claim_type": "hypothesis",
                    "symptom": "the repository-owned unit test command exits nonzero",
                    "causal_claim": (
                        "test_main.py's assertion fails, causing the unit command to fail"
                    ),
                    "supporting_evidence_ids": chosen,
                    "confidence": 0.6,
                    "unknowns": ["exact failing assertion line was not inspected"],
                }
            ]
        }

    def _judge_payload(self, context: str) -> dict[str, Any]:
        finding_id_match = re.search(r"Finding (\S+) \(", context)
        finding_id = finding_id_match.group(1) if finding_id_match else "finding-unknown"
        accept = "all_resolved=True" in context
        return {
            "finding_id": finding_id,
            "verdict": "accept" if accept else "reject",
            "reasons": ["citations fully resolved"] if accept else ["citations not fully resolved"],
        }

    def _strategy_payload(self, request: ModelCompletionRequest, context: str) -> dict[str, Any]:
        finding_ids = _FINDING_LINE.findall(context) or ["finding-unit-failure"]
        evidence_match = re.search(r"evidence=\[(.*?)\]", context)
        evidence_ids = (
            [e.strip().strip("'\"") for e in evidence_match.group(1).split(",") if e.strip()]
            if evidence_match
            else []
        ) or ["fallback-evidence-id"]
        first_pass = ":attempt0:" in request.idempotency_key
        sequence = [2, 1] if (first_pass and self._bad_sequence_first_pass) else [1, 2]
        return {
            "strategies": [
                {
                    "strategy_id": "strategy-fix-assertion",
                    "finding_ids": finding_ids,
                    "title": "Fix the failing assertion",
                    "mechanism": (
                        "Correct the assertion in test_main.py to match the intended behavior."
                    ),
                    "strategy_tradeoffs": "Low risk, directly addresses the failing test.",
                    "phase_templates": [
                        {
                            "phase_id": "phase-1",
                            "sequence": sequence[0],
                            "phase_kind": "diagnostic",
                            "treatment": {
                                "variable": "assertion",
                                "before": "assert False",
                                "after": "assert True",
                            },
                        },
                        {
                            "phase_id": "phase-2",
                            "sequence": sequence[1],
                            "phase_kind": "implementation",
                            "treatment": {
                                "variable": "assertion",
                                "before": "assert False",
                                "after": "assert True",
                            },
                        },
                    ],
                    "risk_ceiling": "code",
                    "evidence_ids": evidence_ids,
                    "assumptions": [],
                    "target_paths": ["tests/test_main.py"],
                }
            ]
        }


def test_a3_20_produces_no_signal_without_matching_guardrail(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path, failing_test=False)
    request_ref = _seed_request(store, allowed_root_id=str(repo.parent), relative_path="repo")
    state = _state(request_ref)

    with _local_worker_ports(store) as ports:
        state = _run_a2_pipeline(store, ports, state)
        a3_runtime = build_a3_runtime(ports=ports)
        state = _advance(a3_runtime, "A3.10", state)
        state = _advance(a3_runtime, "A3.11", state)
        state = _advance(a3_runtime, "A3.20", state)

        signal_set = _model_from_ref(
            store, _ref_by_type(state, "ProblemSignalSet"), ProblemSignalSet
        )
        assert signal_set.signals == []


def test_a3_full_pipeline_reaches_solution_portfolio(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path, failing_test=True)
    guardrail = Guardrail(
        guardrail_id="correctness",
        metric_id="unit_command_result",
        operator="eq",
        threshold=0.0,
        unit="exit_code",
    )
    request_ref = _seed_request(
        store, allowed_root_id=str(repo.parent), relative_path="repo", guardrails=[guardrail]
    )
    state = _state(request_ref)

    with _local_worker_ports(store) as a2_ports:
        state = _run_a2_pipeline(store, a2_ports, state)

    model = _ScriptedModelProvider()
    ports = NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), model=model
    )
    a3_runtime = build_a3_runtime(ports=ports)

    for node_id in A3_NODE_IDS:
        if node_id in {"A3.81", "A3.82", "A3.90"}:
            continue
        state = _advance(a3_runtime, node_id, state)
        if node_id == "A3.20":
            signal_set = _model_from_ref(
                store, _ref_by_type(state, "ProblemSignalSet"), ProblemSignalSet
            )
            assert signal_set.signals, "expected a guardrail-driven signal"
        if node_id == "A3.30":
            branch = _model_from_ref(
                store,
                _stage_ref(state, "A3.30", "AnalyzerObservationBranch"),
                AnalyzerObservationBranch,
            )
            assert branch.unavailable_reason is None
        if node_id == "A3.51":
            finding_set = _model_from_ref(store, _ref_by_type(state, "FindingSet"), FindingSet)
            assert len(finding_set.findings) == 1
            assert finding_set.findings[0].judgement.verdict == "accept"

    # A3.81/A3.90 last, since A3.81 needs the A3.80 output the loop above produced.
    state = _advance(a3_runtime, "A3.81", state)
    assert state["node_routes"]["A3.81"] == "continue"

    state = _advance(a3_runtime, "A3.90", state)
    portfolio_ref = _ref_by_type(state, "SolutionPortfolio")
    portfolio = _model_from_ref(store, portfolio_ref, SolutionPortfolio)
    assert portfolio.strategies
    assert any(s.eligible for s in portfolio.strategies)
    assert state["solution_portfolio_ref"] == portfolio_ref


def test_a3_revision_loop_recovers_from_ineligible_strategy(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path, failing_test=True)
    guardrail = Guardrail(
        guardrail_id="correctness",
        metric_id="unit_command_result",
        operator="eq",
        threshold=0.0,
        unit="exit_code",
    )
    request_ref = _seed_request(
        store, allowed_root_id=str(repo.parent), relative_path="repo", guardrails=[guardrail]
    )
    state = _state(request_ref)

    with _local_worker_ports(store) as a2_ports:
        state = _run_a2_pipeline(store, a2_ports, state)

    model = _ScriptedModelProvider(bad_sequence_on_first_strategy_pass=True)
    ports = NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), model=model
    )
    a3_runtime = build_a3_runtime(ports=ports)

    for node_id in (
        "A3.10",
        "A3.11",
        "A3.20",
        "A3.21",
        "A3.30",
        "A3.31",
        "A3.32",
        "A3.33",
        "A3.40",
        "A3.41",
        "A3.50",
        "A3.51",
    ):
        state = _advance(a3_runtime, node_id, state)

    # Pass 0: bad phase sequence -> A3.61 marks the strategy ineligible.
    for node_id in ("A3.60", "A3.61", "A3.62", "A3.63", "A3.64", "A3.70", "A3.80"):
        state = _advance(a3_runtime, node_id, state)

    strategy_set = _model_from_ref(
        store, _stage_ref(state, "A3.80-pass0", "SolutionStrategySet"), SolutionStrategySet
    )
    assert not any(s.eligible for s in strategy_set.strategies)

    state = _advance(a3_runtime, "A3.81", state)
    assert state["node_routes"]["A3.81"] == "revision"

    state = _advance(a3_runtime, "A3.82", state)
    assert state["node_routes"]["A3.82"] == "continue"
    assert state["a3_revision_attempts"] == 1
    directive = _model_from_ref(store, _ref_by_type(state, "RevisionDirective"), RevisionDirective)
    assert directive.targeted_strategy_ids == ["strategy-fix-assertion"]

    # Pass 1: fixed sequence -> eligible.
    for node_id in ("A3.60", "A3.61", "A3.62", "A3.63", "A3.64", "A3.70", "A3.80"):
        state = _advance(a3_runtime, node_id, state)

    strategy_set_pass1 = _model_from_ref(
        store, _stage_ref(state, "A3.80-pass1", "SolutionStrategySet"), SolutionStrategySet
    )
    assert any(s.eligible for s in strategy_set_pass1.strategies)

    state = _advance(a3_runtime, "A3.81", state)
    assert state["node_routes"]["A3.81"] == "continue"

    state = _advance(a3_runtime, "A3.90", state)
    portfolio = _model_from_ref(store, _ref_by_type(state, "SolutionPortfolio"), SolutionPortfolio)
    assert any(s.eligible for s in portfolio.strategies)


def test_a3_40_repairs_invalid_json_once(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path, failing_test=True)
    guardrail = Guardrail(
        guardrail_id="correctness",
        metric_id="unit_command_result",
        operator="eq",
        threshold=0.0,
        unit="exit_code",
    )
    request_ref = _seed_request(
        store, allowed_root_id=str(repo.parent), relative_path="repo", guardrails=[guardrail]
    )
    state = _state(request_ref)

    with _local_worker_ports(store) as a2_ports:
        state = _run_a2_pipeline(store, a2_ports, state)

    class _FailOnceModel(_ScriptedModelProvider):
        def __init__(self) -> None:
            super().__init__()
            self._first_call = True

        def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
            if ":A3.40:" in request.idempotency_key and self._first_call:
                self._first_call = False
                self.calls.append(request)
                return ModelCompletionResult(
                    request_id="bad",
                    model_id=request.model_id,
                    model_version="scripted-1",
                    raw_text="not json",
                    parsed_json=None,
                    valid_json=False,
                    input_tokens=10,
                    output_tokens=5,
                    stop_reason="refusal",
                )
            return super().complete(request)

    model = _FailOnceModel()
    ports = NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), model=model
    )
    a3_runtime = build_a3_runtime(ports=ports)

    for node_id in ("A3.10", "A3.11", "A3.20", "A3.21", "A3.30", "A3.31", "A3.32", "A3.33"):
        state = _advance(a3_runtime, node_id, state)
    state = _advance(a3_runtime, "A3.40", state)

    assert len(model.calls) == 2  # initial call + one repair
    from production_optimizer.contracts.a3 import FindingDraftSet

    draft_set = _model_from_ref(store, _ref_by_type(state, "FindingDraftSet"), FindingDraftSet)
    assert len(draft_set.drafts) == 1
    assert not draft_set.generation_failures


def test_a3_production_graph_reaches_end(tmp_path: Path) -> None:
    store = _MemoryArtifactStore()
    repo = _seed_python_repo(tmp_path, failing_test=True)
    guardrail = Guardrail(
        guardrail_id="correctness",
        metric_id="unit_command_result",
        operator="eq",
        threshold=0.0,
        unit="exit_code",
    )
    request_ref = _seed_request(
        store, allowed_root_id=str(repo.parent), relative_path="repo", guardrails=[guardrail]
    )
    state = _state(request_ref)

    with _local_worker_ports(store) as a2_ports:
        state = _run_a2_pipeline(store, a2_ports, state)

    model = _ScriptedModelProvider()
    ports = NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy(), model=model
    )
    graph = build_a3_graph(build_a3_runtime(ports=ports))

    result = graph.invoke(state)

    completed = set(result["completed_nodes"])
    assert "A3.90" in completed
    assert result["node_routes"]["A3.81"] == "continue"
    assert result["solution_portfolio_ref"].artifact_type == "SolutionPortfolio"
