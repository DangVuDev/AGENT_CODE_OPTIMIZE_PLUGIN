from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application import NodePorts, build_s03_registrations, build_s03_runtime
from production_optimizer.application.node_runtime import NodeRuntime
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
from production_optimizer.contracts.a2 import RepositoryCommand, RepositoryManifest, SourceSnapshot
from production_optimizer.contracts.a3 import (
    CriterionImpact,
    ExperimentPhaseTemplate,
    ImpactAssessment,
    RiskAssessment,
    RollbackPlan,
    ScopeResolutionEntry,
    ScopeResolutionReport,
    SolutionPortfolio,
    SolutionStrategy,
    TradeoffAnalysis,
    Treatment,
    ValidationPlan,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import (
    IntentRecord,
    IntentStatus,
    ModelCompletionRequest,
    ModelCompletionResult,
    PolicyDecision,
    PolicyRequest,
)
from production_optimizer.contracts.s01 import SelectedSolution, SelectionApproval
from production_optimizer.contracts.s02 import (
    ExecutionPhase,
    ExecutionPlan,
    PlanTask,
    PlanTreatment,
    TaskList,
)
from production_optimizer.contracts.s03 import ExecutionProvenance, PatchArtifact
from production_optimizer.orchestration.catalog import S03_NODE_IDS

_TENANT = "TENANT-S03"
_CASE_ID = "OPT-S03-1"
_ZERO_DIGEST = f"sha256:{'0' * 64}"


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


class _DenyPolicy:
    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=False, decision="deny", policy_version=request.policy_version,
            reasons=["denied for test"],
        )

    def healthcheck(self) -> bool:
        return True


_PATH_LINE = re.compile(r"Authorized write paths: \[(.*?)\]")
_TREATMENT_LINE = re.compile(r"Treatment: \S+ from '(.*?)' to '(.*?)'")


class _ScriptedAgentProvider:
    """Fake `ModelProviderPort` grounded in the real prompt it is given --
    extracts the real authorized path and before/after values (never
    invents its own), then does a real read_file -> write_file -> done
    sequence, mirroring `test_a3_production_handlers._ScriptedModelProvider`.
    """

    def __init__(self) -> None:
        self.calls: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.calls.append(request)
        context = "\n".join(message.content for message in request.messages)
        step = int(request.idempotency_key.rsplit("step", 1)[1])
        path_match = _PATH_LINE.search(context)
        path = path_match.group(1).strip("'\"") if path_match else None

        if step == 1:
            payload: dict[str, Any] | None = {
                "tool": "read_file", "path": path, "summary": "reading current content",
            }
        elif step == 2:
            treatment_match = _TREATMENT_LINE.search(context)
            before, after = treatment_match.groups() if treatment_match else ("", "")
            current_content = request.messages[-1].content
            new_content = current_content.replace(before, after)
            payload = {
                "tool": "write_file", "path": path, "content": new_content,
                "summary": "applying the treatment",
            }
        else:
            payload = {"tool": "done", "summary": "change applied"}

        return ModelCompletionResult(
            request_id=f"req-{len(self.calls)}",
            model_id=request.model_id,
            model_version="scripted-1",
            raw_text=str(payload),
            parsed_json=payload,
            valid_json=True,
            input_tokens=50,
            output_tokens=25,
            stop_reason="end_turn",
        )

    def healthcheck(self) -> bool:
        return True


def _ports(store: _MemoryArtifactStore, *, model: Any = None, policy: Any = None) -> NodePorts:
    return NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), policy=policy or _AllowPolicy(),
        model=model, model_id="scripted-model",
    )


def _advance(runtime: NodeRuntime, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    merged = dict(state)
    merged.update(result)
    merged["artifact_refs"] = [*existing_refs, *new_refs]
    merged["node_routes"] = {**state.get("node_routes", {}), **result.get("node_routes", {})}
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


def _base_envelope_kwargs(artifact_type: str) -> dict[str, Any]:
    return {
        "artifact_id": f"{_CASE_ID}-{artifact_type}",
        "tenant_id": _TENANT,
        "case_id": _CASE_ID,
        "created_at": datetime.now(UTC),
        "producer": ProducerIdentity(name="test", version="1.0"),
        "content_digest": _ZERO_DIGEST,
        "parent_digests": [],
    }


def _seal_and_store(store: _MemoryArtifactStore, model: BaseModel) -> ArtifactRef:
    sealed = model.model_copy(update={"content_digest": model_content_digest(model)})
    artifact_type = cast("Any", sealed).artifact_type
    artifact_id = cast("Any", sealed).artifact_id
    digest = cast("Any", sealed).content_digest
    ref = ArtifactRef(
        artifact_type=artifact_type, schema_version="1.0", artifact_id=artifact_id,
        content_digest=digest, uri=f"memory://{artifact_id}",
    )
    store.seed_json(ref, canonical_json(sealed.model_dump(mode="json")))
    return ref


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _seed_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("TIMEOUT = 30\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _seed_plain_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("TIMEOUT = 30\n")
    return repo


def _strategy(risk_ceiling: str) -> SolutionStrategy:
    return SolutionStrategy(
        strategy_id="strategy-good",
        finding_ids=["finding-1"],
        title="Raise timeout",
        mechanism="Raise the request timeout to reduce spurious failures",
        strategy_tradeoffs="Slightly slower failure detection",
        phase_templates=[
            ExperimentPhaseTemplate(
                phase_id="phase-1", sequence=1, phase_kind="implementation",
                treatment=Treatment(variable="TIMEOUT", before="30", after="60"),
            )
        ],
        risk_ceiling=cast("Any", risk_ceiling),
        risk_assessment=RiskAssessment(
            assessment_id="risk-1", strategy_id="strategy-good",
            risk_tier=cast("Any", risk_ceiling),
            blast_radius="single feature", reversibility="fast", uncertainty=0.1,
            migration_impact=False, security_impact=False,
        ),
        impact_assessment=ImpactAssessment(
            assessment_id="impact-1", strategy_id="strategy-good",
            criterion_impacts=[
                CriterionImpact(
                    criterion_id="latency-p95", direction="improves", confidence=0.9,
                    basis="forecast",
                )
            ],
        ),
        tradeoff_analysis=TradeoffAnalysis(
            analysis_id="tradeoff-1", strategy_id="strategy-good", pros=["fewer timeouts"],
            cons=["slower failure detection"], effort="low", uncertainty=0.1,
        ),
        validation_plan=ValidationPlan(
            plan_id="validation-1", strategy_id="strategy-good",
            benchmark_protocol="rerun the suite", stop_conditions=["still failing"],
        ),
        rollback_plan=RollbackPlan(
            plan_id="rollback-1", strategy_id="strategy-good", mechanism="revert the commit",
            verification="rerun the suite", reversible=True,
        ),
        scope_resolution=ScopeResolutionReport(
            report_id="scope-1", strategy_id="strategy-good",
            entries=[ScopeResolutionEntry(path_or_symbol="src/app.py", kind="file", exists=True)],
            fully_resolved=True,
        ),
        evidence_ids=["evidence-1"],
        eligible=True,
    )


def _seed_case(
    store: _MemoryArtifactStore, *, repo: Path, risk_ceiling: str, git_revision: str | None
) -> list[ArtifactRef]:
    fingerprint = sha256_digest(canonical_json({"case_id": _CASE_ID, "origin": "manual"}))
    request = OptimizationRequest(
        **_base_envelope_kwargs("OptimizationRequest"),
        origin=Origin.MANUAL, scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="app-repo", allowed_root_id="workspace", relative_path=".",
            requested_revision="HEAD",
        ),
        objective=Objective(statement="Reduce spurious timeouts", feature_id="app"),
        criteria=[
            Criterion(
                criterion_id="latency-p95", metric_id="p95_latency_ms", direction="minimize",
                target=180.0, unit="ms", weight=1.0,
            )
        ],
        workload=WorkloadContract(
            workload_id="app-workload", environment_id="staging", repetitions=3, warmup_runs=1,
            concurrency=1, cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="latency-samples", criterion_id="latency-p95",
                accepted_source_types={"benchmark"}, minimum_samples=1,
            )
        ],
        budget=ExecutionBudget(
            deadline_seconds=300, maximum_worker_seconds=120, maximum_model_tokens=2048,
            maximum_storage_bytes=10_000_000,
        ),
        approval=ApprovalBinding(
            approval_id="approval-1", actor_id="owner-1", actor_role="owner", decision="approve",
            artifact_digest=fingerprint, policy_version="test-approval-v1",
        ),
        request_fingerprint=fingerprint,
    )
    request_ref = _seal_and_store(store, request)

    snapshot = SourceSnapshot(
        **_base_envelope_kwargs("SourceSnapshot"),
        repository_id="app-repo", canonical_path_ref=str(repo), git_revision=git_revision,
        dirty=False, files=[],
    )
    snapshot_ref = _seal_and_store(store, snapshot)

    manifest = RepositoryManifest(
        **_base_envelope_kwargs("RepositoryManifest"),
        languages={"python": 1.0}, modules=["src"], manifest_files=[], test_roots=["tests"],
        commands=[
            RepositoryCommand(
                command_id="unit-1", argv=["python", "-m", "pytest", "tests"],
                working_directory=str(repo), kind="unit", source="pyproject_toml",
            )
        ],
        tool_coverage={},
    )
    manifest_ref = _seal_and_store(store, manifest)

    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=_ZERO_DIGEST, quality_report_digest=_ZERO_DIGEST,
        strategies=[_strategy(risk_ceiling)],
    )
    portfolio_ref = _seal_and_store(store, portfolio)

    selected = SelectedSolution(
        **{
            **_base_envelope_kwargs("SelectedSolution"),
            # Pass-scoped to match real S01.90 output (BR-01-005); pass 0 is
            # a fresh case, which is what this fixture simulates.
            "artifact_id": f"{_CASE_ID}-S01.90-pass0-SelectedSolution",
        },
        ranking_result_digest=_ZERO_DIGEST, solution_portfolio_digest=portfolio_ref.content_digest,
        strategy_id="strategy-good",
        approval=SelectionApproval(decision="auto_selected", policy_version="s01-ranking-v1"),
    )
    selected_ref = _seal_and_store(store, selected)

    phase = ExecutionPhase(
        phase_id="phase-1", sequence=1, phase_kind="implementation",
        treatment=PlanTreatment(variable="TIMEOUT", before="30", after="60"),
        done_criteria=["latency-p95 improves"], rollback_command="git checkout -- src/app.py",
        rollback_trigger="latency regresses", rollback_deadline_seconds=600,
    )
    plan = ExecutionPlan(
        **_base_envelope_kwargs("ExecutionPlan"),
        selected_solution_digest=selected_ref.content_digest, phases=[phase],
    )
    plan_ref = _seal_and_store(store, plan)

    task = PlanTask(
        task_id="task-1", phase_id="phase-1", objective="Raise the timeout constant",
        files=["src/app.py"], instructions="Change TIMEOUT from 30 to 60 in src/app.py",
        owner="app-team",
    )
    task_list = TaskList(
        **_base_envelope_kwargs("TaskList"), execution_plan_digest=plan_ref.content_digest,
        tasks=[task],
    )
    task_list_ref = _seal_and_store(store, task_list)

    return [
        request_ref, snapshot_ref, manifest_ref, portfolio_ref, selected_ref, plan_ref,
        task_list_ref,
    ]


def _state(refs: list[ArtifactRef]) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID, "thread_id": "THREAD-S03-1", "tenant_id": _TENANT, "lane": "manual",
        "artifact_refs": refs,
    }


def test_s03_registrations_cover_every_node() -> None:
    registrations = build_s03_registrations()
    assert set(registrations) == set(S03_NODE_IDS)


def test_s03_deterministic_executor_edits_a_real_git_worktree_leaving_original_untouched(
    tmp_path: Path,
) -> None:
    repo = _seed_git_repo(tmp_path)
    original_content = (repo / "src" / "app.py").read_text()
    store = _MemoryArtifactStore()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    refs = _seed_case(store, repo=repo, risk_ceiling="experiment_config", git_revision=head)
    runtime = build_s03_runtime(ports=_ports(store))
    state = _state(refs)

    routes: dict[str, str] = {}
    for node_id in S03_NODE_IDS:
        state = _advance(runtime, node_id, state)
        routes[node_id] = state["node_routes"][node_id]

    assert routes["S03.10"] == "continue"
    assert routes["S03.60"] == "continue"
    assert routes["S03.70"] == "continue"

    provenance = _model_from_ref(
        store, _ref_by_type(state, "ExecutionProvenance"), ExecutionProvenance
    )
    assert provenance.isolation == "git_worktree"
    assert provenance.executor_kind == "deterministic_config"

    patch = _model_from_ref(store, _ref_by_type(state, "PatchArtifact"), PatchArtifact)
    assert patch.changed_files == ["src/app.py"]
    assert "TIMEOUT = 60" in patch.diff
    assert "-TIMEOUT = 30" in patch.diff
    assert patch.scope_report.in_scope is True
    assert patch.sanitation_report.passed is True

    # The real, original repository was never touched -- isolation actually
    # isolated, not just claimed to.
    assert (repo / "src" / "app.py").read_text() == original_content

    # S03.90 deliberately leaves the workspace on disk for S04 to reuse
    # directly (see its docstring) -- this test stops before S04, so it
    # cleans up after itself instead of leaking a temp directory.
    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])
    assert workspace_path.exists()
    shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s03_llm_driven_executor_edits_the_authorized_file_via_the_real_tool_loop(
    tmp_path: Path,
) -> None:
    repo = _seed_plain_repo(tmp_path)
    store = _MemoryArtifactStore()
    refs = _seed_case(store, repo=repo, risk_ceiling="code", git_revision=None)
    model = _ScriptedAgentProvider()
    runtime = build_s03_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    for node_id in S03_NODE_IDS:
        state = _advance(runtime, node_id, state)

    provenance = _model_from_ref(
        store, _ref_by_type(state, "ExecutionProvenance"), ExecutionProvenance
    )
    assert provenance.isolation == "directory_copy"
    assert provenance.executor_kind == "llm_driven"
    assert [call.tool for call in provenance.tool_calls] == ["read_file", "write_file", "done"]

    patch = _model_from_ref(store, _ref_by_type(state, "PatchArtifact"), PatchArtifact)
    assert "TIMEOUT = 60" in patch.diff
    assert (repo / "src" / "app.py").read_text() == "TIMEOUT = 30\n"

    workspace_path = Path(cast("dict[str, Any]", state["s03_workspace"])["path"])
    shutil.rmtree(workspace_path.parent, ignore_errors=True)


def test_s03_10_fails_closed_when_policy_denies(tmp_path: Path) -> None:
    repo = _seed_plain_repo(tmp_path)
    store = _MemoryArtifactStore()
    refs = _seed_case(store, repo=repo, risk_ceiling="experiment_config", git_revision=None)
    runtime = build_s03_runtime(ports=_ports(store, policy=_DenyPolicy()))
    state = _state(refs)

    state = _advance(runtime, "S03.10", state)

    assert state["node_routes"]["S03.10"] == "rejected"
    assert state["s03_authorization"]["authorized"] is False
