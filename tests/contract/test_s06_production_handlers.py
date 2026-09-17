# pyright: reportPrivateUsage=false

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, TypeAdapter

from production_optimizer.adapters.production.bootstrap_policies import build_bootstrap_policy
from production_optimizer.application import (
    NodePorts,
    build_s03_runtime,
    build_s04_runtime,
    build_s05_runtime,
    build_s06_registrations,
    build_s06_runtime,
)
from production_optimizer.application.node_runtime import NodeRuntime, route_for
from production_optimizer.application.s05_worker_capabilities import BENCHMARK_JSON_FILENAME
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
from production_optimizer.contracts.a2 import (
    BaselineSnapshot,
    MetricAggregate,
    RepositoryCommand,
    RepositoryManifest,
    SourceSnapshot,
)
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
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.platform import (
    IntentRecord,
    IntentStatus,
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
from production_optimizer.contracts.s06 import Decision, RepositoryApplyResult, RollbackReport
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.catalog import (
    S03_NODE_IDS,
    S04_NODE_IDS,
    S05_NODE_IDS,
    S06_NODE_IDS,
)
from production_optimizer.orchestration.shared_workflow import _policy_outcome

_TENANT = "TENANT-S06"
_CASE_ID = "OPT-S06-1"
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


def _ports(store: _MemoryArtifactStore) -> NodePorts:
    return NodePorts(artifacts=store, intents=_MemoryIntentLedger(), policy=_AllowPolicy())


def _real_policy_ports(store: _MemoryArtifactStore) -> NodePorts:
    """Like `_ports`, but with the real, fail-closed bootstrap policy instead
    of `_AllowPolicy` -- needed for S06.90 tests that exercise the real
    `apply_to_real_repo` decision (always `require_approval` today)."""

    return NodePorts(
        artifacts=store,
        intents=_MemoryIntentLedger(),
        policy=build_bootstrap_policy(policy_version="test-policy-v1"),
    )


def _advance(runtime: NodeRuntime, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    merged = dict(state)
    merged.update(result)
    merged["artifact_refs"] = [*existing_refs, *new_refs]
    merged["node_routes"] = {**state.get("node_routes", {}), **result.get("node_routes", {})}
    if "s03_revision_attempts" in result:
        merged["s03_revision_attempts"] = (
            state.get("s03_revision_attempts", 0) + result["s03_revision_attempts"]
        )
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


def _try_ref_by_type(state: dict[str, Any], artifact_type: str) -> ArtifactRef | None:
    for ref in cast("list[ArtifactRef]", state["artifact_refs"]):
        if ref.artifact_type == artifact_type:
            return ref
    return None


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
        artifact_type=artifact_type,
        schema_version="1.0",
        artifact_id=artifact_id,
        content_digest=digest,
        uri=f"memory://{artifact_id}",
    )
    store.seed_json(ref, canonical_json(sealed.model_dump(mode="json")))
    return ref


def _seed_repo(tmp_path: Path, *, with_benchmark: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def compute():\n    return 1 + 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "from app import compute\n\n\ndef test_compute():\n    assert compute() == 4\n"
    )
    if with_benchmark:
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "bench-demo"\ndependencies = ["pytest-benchmark>=4.0"]\n'
        )
        (repo / "tests" / "test_bench.py").write_text(
            "from app import compute\n\n\n"
            "def test_compute_benchmark(benchmark):\n"
            "    benchmark(compute)\n"
        )
    return repo


def _seed_git_repo(tmp_path: Path, *, with_benchmark: bool) -> tuple[Path, str]:
    """Like `_seed_repo`, but a real git repository with one commit, so
    `SourceSnapshot.git_revision` is real and S06.90 has an actual `HEAD` to
    branch/apply/commit against (the plain-directory fixture above exercises
    S03's `directory_copy` isolation path, not `git_worktree`).

    Deliberately does NOT pin `core.autocrlf` here: `_apply_patch_to_real_repo`
    itself is robust to whatever line-ending convention the real repository
    already uses (a real, empirically-found gap -- `_build_unified_diff`'s
    diff is always LF-only regardless of the file's actual on-disk bytes,
    since `Path.read_text()` normalizes on read; `git apply -c
    core.autocrlf=true` is what makes that apply correctly against a real
    CRLF file). Leaving this fixture's git config at whatever the host
    defaults to is exactly what proves that robustness, rather than
    papering over it by controlling the test environment."""

    repo = _seed_repo(tmp_path, with_benchmark=with_benchmark)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, head


def _pytest_benchmark_available_on_path() -> bool:
    probe = subprocess.run(
        ["python", "-c", "import pytest_benchmark"], capture_output=True, timeout=30, check=False
    )
    return probe.returncode == 0


def _benchmark_command(repo: Path) -> RepositoryCommand:
    return RepositoryCommand(
        command_id="benchmark-1",
        argv=[
            "python",
            "-m",
            "pytest",
            "--benchmark-only",
            f"--benchmark-json={BENCHMARK_JSON_FILENAME}",
        ],
        working_directory=str(repo),
        kind="benchmark",
        source="pyproject_toml",
    )


def _strategy() -> SolutionStrategy:
    return SolutionStrategy(
        strategy_id="strategy-good",
        finding_ids=["finding-1"],
        title="Fix compute",
        mechanism="Correct the arithmetic in compute()",
        strategy_tradeoffs="None",
        phase_templates=[
            ExperimentPhaseTemplate(
                phase_id="phase-1",
                sequence=1,
                phase_kind="implementation",
                treatment=Treatment(variable="compute_body", before="1 + 1", after="2 + 2"),
            )
        ],
        risk_ceiling=cast("Any", "experiment_config"),
        risk_assessment=RiskAssessment(
            assessment_id="risk-1",
            strategy_id="strategy-good",
            risk_tier=cast("Any", "experiment_config"),
            blast_radius="single feature",
            reversibility="fast",
            uncertainty=0.1,
            migration_impact=False,
            security_impact=False,
        ),
        impact_assessment=ImpactAssessment(
            assessment_id="impact-1",
            strategy_id="strategy-good",
            criterion_impacts=[
                CriterionImpact(
                    criterion_id="correctness",
                    direction="improves",
                    confidence=0.9,
                    basis="forecast",
                )
            ],
        ),
        tradeoff_analysis=TradeoffAnalysis(
            analysis_id="tradeoff-1",
            strategy_id="strategy-good",
            pros=["fixes the bug"],
            cons=["small chance of unrelated regressions"],
            effort="low",
            uncertainty=0.1,
        ),
        validation_plan=ValidationPlan(
            plan_id="validation-1",
            strategy_id="strategy-good",
            benchmark_protocol="rerun the suite",
            stop_conditions=["still failing"],
        ),
        rollback_plan=RollbackPlan(
            plan_id="rollback-1",
            strategy_id="strategy-good",
            mechanism="revert the commit",
            verification="rerun the suite",
            reversible=True,
        ),
        scope_resolution=ScopeResolutionReport(
            report_id="scope-1",
            strategy_id="strategy-good",
            entries=[ScopeResolutionEntry(path_or_symbol="app.py", kind="file", exists=True)],
            fully_resolved=True,
        ),
        evidence_ids=["evidence-1"],
        eligible=True,
    )


def _seed_case(
    store: _MemoryArtifactStore,
    *,
    repo: Path,
    criteria: list[Criterion],
    guardrails: list[Guardrail] | None = None,
    extra_commands: list[RepositoryCommand] | None = None,
    extra_baseline_aggregates: list[MetricAggregate] | None = None,
    git_revision: str | None = None,
) -> list[ArtifactRef]:
    fingerprint = sha256_digest(canonical_json({"case_id": _CASE_ID, "origin": "manual"}))
    request = OptimizationRequest(
        **_base_envelope_kwargs("OptimizationRequest"),
        origin=Origin.MANUAL,
        scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="app-repo",
            allowed_root_id="workspace",
            relative_path=".",
            requested_revision="HEAD",
        ),
        objective=Objective(statement="Fix compute()", feature_id="app"),
        criteria=criteria,
        guardrails=guardrails or [],
        workload=WorkloadContract(
            workload_id="app-workload",
            environment_id="staging",
            repetitions=1,
            warmup_runs=0,
            concurrency=1,
            cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id=f"{c.criterion_id}-samples",
                criterion_id=c.criterion_id,
                accepted_source_types={"benchmark"},
                minimum_samples=1,
            )
            for c in criteria
        ],
        budget=ExecutionBudget(
            deadline_seconds=300,
            maximum_worker_seconds=120,
            maximum_model_tokens=2048,
            maximum_storage_bytes=10_000_000,
        ),
        approval=ApprovalBinding(
            approval_id="approval-1",
            actor_id="owner-1",
            actor_role="owner",
            decision="approve",
            artifact_digest=fingerprint,
            policy_version="test-approval-v1",
        ),
        request_fingerprint=fingerprint,
    )
    request_ref = _seal_and_store(store, request)

    snapshot = SourceSnapshot(
        **_base_envelope_kwargs("SourceSnapshot"),
        repository_id="app-repo",
        canonical_path_ref=str(repo),
        git_revision=git_revision,
        dirty=False,
        files=[],
    )
    snapshot_ref = _seal_and_store(store, snapshot)

    manifest = RepositoryManifest(
        **_base_envelope_kwargs("RepositoryManifest"),
        languages={"python": 1.0},
        modules=["."],
        manifest_files=[],
        test_roots=["tests"],
        commands=[
            RepositoryCommand(
                command_id="unit-1",
                argv=["python", "-m", "pytest", "tests", "--benchmark-skip"]
                if extra_commands
                else ["python", "-m", "pytest", "tests"],
                working_directory=str(repo),
                kind="unit",
                source="pyproject_toml",
            ),
            *(extra_commands or []),
        ],
        tool_coverage={},
    )
    manifest_ref = _seal_and_store(store, manifest)

    baseline = BaselineSnapshot(
        **_base_envelope_kwargs("BaselineSnapshot"),
        request_digest=request_ref.content_digest,
        source_snapshot_digest=snapshot_ref.content_digest,
        workload_id="app-workload",
        environment_id="staging",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 1, tzinfo=UTC),
        aggregates=[
            MetricAggregate(
                metric_id="unit_command_result",
                unit="exit_code",
                sample_ids=["s1"],
                count=1,
                minimum=1.0,
                maximum=1.0,
                mean=1.0,
            ),
            *(extra_baseline_aggregates or []),
        ],
    )
    baseline_ref = _seal_and_store(store, baseline)

    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=_ZERO_DIGEST,
        quality_report_digest=_ZERO_DIGEST,
        strategies=[_strategy()],
    )
    portfolio_ref = _seal_and_store(store, portfolio)

    selected = SelectedSolution(
        **{
            **_base_envelope_kwargs("SelectedSolution"),
            # Pass-scoped to match real S01.90 output (BR-01-005); pass 0 is
            # a fresh case (state seeds no `s01_excluded_strategy_ids`),
            # which is what this fixture simulates.
            "artifact_id": f"{_CASE_ID}-S01.90-pass0-SelectedSolution",
        },
        ranking_result_digest=_ZERO_DIGEST,
        solution_portfolio_digest=portfolio_ref.content_digest,
        strategy_id="strategy-good",
        approval=SelectionApproval(decision="auto_selected", policy_version="s01-ranking-v1"),
    )
    selected_ref = _seal_and_store(store, selected)

    phase = ExecutionPhase(
        phase_id="phase-1",
        sequence=1,
        phase_kind="implementation",
        risk_tier="code",
        treatment=PlanTreatment(variable="compute_body", before="1 + 1", after="2 + 2"),
        done_criteria=["correctness improves"],
        rollback_command="git checkout -- app.py",
        rollback_trigger="tests regress",
        rollback_deadline_seconds=600,
    )
    plan = ExecutionPlan(
        **_base_envelope_kwargs("ExecutionPlan"),
        selected_solution_digest=selected_ref.content_digest,
        phases=[phase],
    )
    plan_ref = _seal_and_store(store, plan)

    task = PlanTask(
        task_id="task-1",
        phase_id="phase-1",
        objective="Fix the arithmetic bug",
        files=["app.py"],
        instructions="Change 1 + 1 to 2 + 2 in app.py",
        owner="app-team",
    )
    task_list = TaskList(
        **_base_envelope_kwargs("TaskList"),
        execution_plan_digest=plan_ref.content_digest,
        tasks=[task],
    )
    task_list_ref = _seal_and_store(store, task_list)

    return [
        request_ref,
        snapshot_ref,
        manifest_ref,
        baseline_ref,
        portfolio_ref,
        selected_ref,
        plan_ref,
        task_list_ref,
    ]


def _state(refs: list[ArtifactRef]) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID,
        "thread_id": "THREAD-S06-1",
        "tenant_id": _TENANT,
        "lane": "manual",
        "artifact_refs": refs,
    }


def _run_through_s05(store: _MemoryArtifactStore, state: dict[str, Any]) -> dict[str, Any]:
    for node_ids, builder in (
        (S03_NODE_IDS, build_s03_runtime),
        (S04_NODE_IDS, build_s04_runtime),
        (S05_NODE_IDS, build_s05_runtime),
    ):
        runtime = builder(ports=_ports(store))
        for node_id in node_ids:
            state = _advance(runtime, node_id, state)
            if state["node_routes"][node_id] == "rejected":
                raise AssertionError(f"{node_id} unexpectedly rejected: {state}")
    return state


def _run_s06(store: _MemoryArtifactStore, state: dict[str, Any]) -> dict[str, Any]:
    runtime = build_s06_runtime(ports=_ports(store))
    for node_id in S06_NODE_IDS:
        state = _advance(runtime, node_id, state)
        if state["node_routes"][node_id] == "rejected":
            break
    return state


def test_s06_registrations_cover_every_node() -> None:
    registrations = build_s06_registrations()
    assert set(registrations) == set(S06_NODE_IDS)


def test_s06_keeps_when_the_sole_criterion_meets_target(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path, with_benchmark=False)
    store = _MemoryArtifactStore()
    correctness = Criterion(
        criterion_id="correctness",
        metric_id="unit_command_result",
        direction="minimize",
        target=0.0,
        unit="exit_code",
        weight=1.0,
    )
    refs = _seed_case(store, repo=repo, criteria=[correctness])
    state = _run_through_s05(store, _state(refs))
    state = _run_s06(store, state)

    assert state["s06_decision"]["outcome"] == "KEEP"
    decision = _model_from_ref(store, _ref_by_type(state, "Decision"), Decision)
    assert decision.outcome == "KEEP"
    assert decision.attribution.classification == "all_targets_met"
    assert decision.rollback_report_digest is None
    assert _try_ref_by_type(state, "RollbackReport") is None
    assert state.get("s03_revision_attempts", 0) == 0


def test_s06_fix_one_part_when_one_of_two_criteria_is_repairable(tmp_path: Path) -> None:
    if not _pytest_benchmark_available_on_path():
        pytest.skip("pytest-benchmark not importable via the 'python' resolved on PATH")

    repo = _seed_repo(tmp_path, with_benchmark=True)
    store = _MemoryArtifactStore()
    correctness = Criterion(
        criterion_id="correctness",
        metric_id="unit_command_result",
        direction="minimize",
        target=0.0,
        unit="exit_code",
        weight=1.0,
    )
    # An impossible target: any real, positive measured duration fails it,
    # deterministically, regardless of actual machine speed.
    performance = Criterion(
        criterion_id="performance",
        metric_id="benchmark_command_result",
        direction="minimize",
        target=0.0,
        unit="seconds",
        weight=1.0,
    )
    benchmark_command = _benchmark_command(repo)
    refs = _seed_case(
        store,
        repo=repo,
        criteria=[correctness, performance],
        extra_commands=[benchmark_command],
        extra_baseline_aggregates=[
            MetricAggregate(
                metric_id="benchmark_command_result",
                unit="seconds",
                sample_ids=["b1"],
                count=1,
                minimum=0.0001,
                maximum=0.0001,
                mean=0.0001,
            )
        ],
    )
    state = _run_through_s05(store, _state(refs))
    state = _run_s06(store, state)

    assert state["s06_decision"]["outcome"] == "FIX_ONE_PART"
    decision = _model_from_ref(store, _ref_by_type(state, "Decision"), Decision)
    assert decision.outcome == "FIX_ONE_PART"
    assert decision.attribution.classification == "repairable"
    met = {t.criterion_id: t.met for t in decision.target_evaluations}
    assert met == {"correctness": True, "performance": False}
    assert _try_ref_by_type(state, "RollbackReport") is None
    # The real, load-bearing effect of FIX_ONE_PART: a genuine retry of S03
    # for the same phase needs a bumped counter (see contracts/state.py).
    assert state["s03_revision_attempts"] == 1


def test_s06_reverts_and_excludes_the_strategy_when_no_evidence_of_improvement(
    tmp_path: Path,
) -> None:
    if not _pytest_benchmark_available_on_path():
        pytest.skip("pytest-benchmark not importable via the 'python' resolved on PATH")

    repo = _seed_repo(tmp_path, with_benchmark=True)
    store = _MemoryArtifactStore()
    performance = Criterion(
        criterion_id="performance",
        metric_id="benchmark_command_result",
        direction="minimize",
        target=0.0,
        unit="seconds",
        weight=1.0,
    )
    benchmark_command = _benchmark_command(repo)
    refs = _seed_case(
        store,
        repo=repo,
        criteria=[performance],
        extra_commands=[benchmark_command],
        extra_baseline_aggregates=[
            MetricAggregate(
                metric_id="benchmark_command_result",
                unit="seconds",
                sample_ids=["b1"],
                count=1,
                minimum=0.0001,
                maximum=0.0001,
                mean=0.0001,
            )
        ],
    )
    state = _run_through_s05(store, _state(refs))
    state = _run_s06(store, state)

    assert state["s06_decision"]["outcome"] == "REVERT"
    decision = _model_from_ref(store, _ref_by_type(state, "Decision"), Decision)
    assert decision.outcome == "REVERT"
    assert decision.attribution.classification == "wrong_direction"
    assert decision.rollback_report_digest is not None

    rollback = _model_from_ref(store, _ref_by_type(state, "RollbackReport"), RollbackReport)
    assert rollback.original_untouched is True  # directory-copy isolation, nothing to undo

    assert state["s01_excluded_strategy_ids"] == ["strategy-good"]


def _advance_repo_head(repo: Path) -> None:
    (repo / "unrelated.txt").write_text("drift\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "unrelated drift"], cwd=repo, check=True)


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


_BROKEN_DIFF = (
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def compute():\n"
    "-    return 999 + 999\n"
    "+    return 2 + 2\n"
)


def _mutate_patch_diff(store: _MemoryArtifactStore, ref: ArtifactRef, *, new_diff: str) -> None:
    """White-box mutation: overwrite a sealed `PatchArtifact`'s stored bytes
    at its existing URI, in place -- `_read_model` never re-verifies a
    ref's `content_digest` against its content, so a later read through the
    same `ref` sees `new_diff` without needing a new artifact_id/URI. Used
    to isolate "the diff no longer applies cleanly" from "the repository
    HEAD drifted" (test 2 above already covers the latter)."""

    content = store.read(tenant_id=_TENANT, ref=ref)
    data = TypeAdapter(dict[str, Any]).validate_json(content)
    data["diff"] = new_diff
    store.seed_json(ref, canonical_json(data))


def test_s06_90_applies_the_patch_on_a_real_keep(tmp_path: Path) -> None:
    repo, head = _seed_git_repo(tmp_path, with_benchmark=False)
    store = _MemoryArtifactStore()
    correctness = Criterion(
        criterion_id="correctness",
        metric_id="unit_command_result",
        direction="minimize",
        target=0.0,
        unit="exit_code",
        weight=1.0,
    )
    refs = _seed_case(store, repo=repo, criteria=[correctness], git_revision=head)
    state = _run_through_s05(store, _state(refs))
    state = _run_s06(store, state)

    assert state["s06_decision"]["outcome"] == "KEEP"
    assert state["node_routes"]["S06.90"] == "continue"
    result = _model_from_ref(
        store, _ref_by_type(state, "RepositoryApplyResult"), RepositoryApplyResult
    )
    assert result.applied is True
    assert result.approval_decision == "auto_allowed"  # _AllowPolicy -> policy_decision "allow"
    assert result.branch_name == f"optimizer/{_CASE_ID}-phase-1"
    assert result.commit_sha is not None
    assert len(result.commit_sha) == 40
    assert result.branch_name is not None

    # Prove the commit is real, in the real repo, on the real branch --
    # not just a claim in the sealed artifact.
    branch_head = _git_output(repo, "rev-parse", result.branch_name)
    assert branch_head == result.commit_sha
    # The real, original checkout was never touched.
    assert _git_output(repo, "status", "--porcelain") == ""
    assert (repo / "app.py").read_text() == "def compute():\n    return 1 + 1\n"
    # Cleanup ran even on success -- no leftover worktree entry.
    worktree_list = _git_output(repo, "worktree", "list").splitlines()
    assert len(worktree_list) == 1


def test_s06_90_fails_closed_when_base_revision_has_drifted(tmp_path: Path) -> None:
    repo, head = _seed_git_repo(tmp_path, with_benchmark=False)
    _advance_repo_head(repo)  # real repo HEAD moves past `head` before S06 ever runs
    store = _MemoryArtifactStore()
    correctness = Criterion(
        criterion_id="correctness",
        metric_id="unit_command_result",
        direction="minimize",
        target=0.0,
        unit="exit_code",
        weight=1.0,
    )
    refs = _seed_case(store, repo=repo, criteria=[correctness], git_revision=head)
    state = _run_through_s05(store, _state(refs))
    state = _run_s06(store, state)

    assert state["s06_decision"]["outcome"] == "KEEP"  # the decision itself is unaffected
    assert state["node_routes"]["S06.90"] == "continue"  # fails closed, does not fail the case
    result = _model_from_ref(
        store, _ref_by_type(state, "RepositoryApplyResult"), RepositoryApplyResult
    )
    assert result.applied is False
    assert result.branch_name is None
    assert result.commit_sha is None
    assert result.failure_reason is not None
    assert "base_revision" in result.failure_reason
    # No branch was ever created.
    assert f"optimizer/{_CASE_ID}-phase-1" not in _git_output(repo, "branch", "--list")


def test_s06_90_fails_closed_when_git_apply_check_fails(tmp_path: Path) -> None:
    repo, head = _seed_git_repo(tmp_path, with_benchmark=False)
    store = _MemoryArtifactStore()
    correctness = Criterion(
        criterion_id="correctness",
        metric_id="unit_command_result",
        direction="minimize",
        target=0.0,
        unit="exit_code",
        weight=1.0,
    )
    refs = _seed_case(store, repo=repo, criteria=[correctness], git_revision=head)
    state = _run_through_s05(store, _state(refs))

    patch_ref = _ref_by_type(state, "PatchArtifact")
    _mutate_patch_diff(store, patch_ref, new_diff=_BROKEN_DIFF)

    state = _run_s06(store, state)

    assert state["s06_decision"]["outcome"] == "KEEP"
    assert state["node_routes"]["S06.90"] == "continue"
    result = _model_from_ref(
        store, _ref_by_type(state, "RepositoryApplyResult"), RepositoryApplyResult
    )
    assert result.applied is False
    assert result.failure_reason is not None
    assert "apply --check" in result.failure_reason
    assert f"optimizer/{_CASE_ID}-phase-1" not in _git_output(repo, "branch", "--list")


def test_s06_90_is_a_noop_for_non_keep_outcomes(tmp_path: Path) -> None:
    if not _pytest_benchmark_available_on_path():
        pytest.skip("pytest-benchmark not importable via the 'python' resolved on PATH")

    repo = _seed_repo(tmp_path, with_benchmark=True)
    store = _MemoryArtifactStore()
    performance = Criterion(
        criterion_id="performance",
        metric_id="benchmark_command_result",
        direction="minimize",
        target=0.0,
        unit="seconds",
        weight=1.0,
    )
    benchmark_command = _benchmark_command(repo)
    refs = _seed_case(
        store,
        repo=repo,
        criteria=[performance],
        extra_commands=[benchmark_command],
        extra_baseline_aggregates=[
            MetricAggregate(
                metric_id="benchmark_command_result",
                unit="seconds",
                sample_ids=["b1"],
                count=1,
                minimum=0.0001,
                maximum=0.0001,
                mean=0.0001,
            )
        ],
    )
    state = _run_through_s05(store, _state(refs))
    state = _run_s06(store, state)

    assert state["s06_decision"]["outcome"] == "REVERT"
    assert state["node_routes"]["S06.90"] == "continue"
    assert state["s06_apply"] == {"applied": False, "attempted": False}
    assert _try_ref_by_type(state, "RepositoryApplyResult") is None


def test_s06_90_halts_for_approval_when_policy_requires_it(tmp_path: Path) -> None:
    repo, head = _seed_git_repo(tmp_path, with_benchmark=False)
    store = _MemoryArtifactStore()
    correctness = Criterion(
        criterion_id="correctness",
        metric_id="unit_command_result",
        direction="minimize",
        target=0.0,
        unit="exit_code",
        weight=1.0,
    )
    refs = _seed_case(store, repo=repo, criteria=[correctness], git_revision=head)
    state = _state(refs)
    for node_ids, builder in (
        (S03_NODE_IDS, build_s03_runtime),
        (S04_NODE_IDS, build_s04_runtime),
        (S05_NODE_IDS, build_s05_runtime),
    ):
        runtime = builder(ports=_real_policy_ports(store))
        for node_id in node_ids:
            state = _advance(runtime, node_id, state)
            if state["node_routes"][node_id] == "rejected":
                raise AssertionError(f"{node_id} unexpectedly rejected: {state}")

    runtime = build_s06_runtime(ports=_real_policy_ports(store))
    for node_id in S06_NODE_IDS:
        state = _advance(runtime, node_id, state)

    assert state["s06_decision"]["outcome"] == "KEEP"
    assert state["node_routes"]["S06.90"] == "approval"
    assert state["pending_interrupt"] is not None
    assert state["pending_interrupt"].stage == "S06.90"
    assert state["s06_apply"] == {"applied": False, "attempted": False}
    # The regression test for the shared_workflow.py routing bug fix: the
    # outer graph must see "halt", not "keep", while S06.90's own approval
    # is still pending.
    assert _policy_outcome(cast("OptimizationState", state)) == "halt"


def test_s06_90_applies_after_a_real_resume_approval(tmp_path: Path) -> None:
    repo, head = _seed_git_repo(tmp_path, with_benchmark=False)
    store = _MemoryArtifactStore()
    correctness = Criterion(
        criterion_id="correctness",
        metric_id="unit_command_result",
        direction="minimize",
        target=0.0,
        unit="exit_code",
        weight=1.0,
    )
    refs = _seed_case(store, repo=repo, criteria=[correctness], git_revision=head)
    state = _state(refs)
    for node_ids, builder in (
        (S03_NODE_IDS, build_s03_runtime),
        (S04_NODE_IDS, build_s04_runtime),
        (S05_NODE_IDS, build_s05_runtime),
    ):
        runtime = builder(ports=_real_policy_ports(store))
        for node_id in node_ids:
            state = _advance(runtime, node_id, state)
            if state["node_routes"][node_id] == "rejected":
                raise AssertionError(f"{node_id} unexpectedly rejected: {state}")

    s06_runtime = build_s06_runtime(ports=_real_policy_ports(store))
    for node_id in S06_NODE_IDS:
        state = _advance(s06_runtime, node_id, state)
    assert state["node_routes"]["S06.90"] == "approval"

    state["resume_command"] = ResumeInterruptCommand(
        command_id="resume-1",
        tenant_id=_TENANT,
        case_id=_CASE_ID,
        thread_id="THREAD-S06-1",
        interrupt_id=state["pending_interrupt"].interrupt_id,
        actor_id="owner-1",
        actor_roles={"owner"},
        decision="approve",
        artifact_digest=state["pending_interrupt"].artifact_digest,
        policy_version=state["pending_interrupt"].policy_version,
        issued_at=datetime.now(UTC),
    )
    # Mirrors `application.resume.resume_case` exactly: `resume_attempts`
    # scoped to `resume_target_node` is what busts S06.90's own idempotency
    # key (see `node_runtime._derive_idempotency_key`'s docstring) -- without
    # it, `NodeRuntime` would see the same key as the halted call and just
    # replay the cached "approval" result forever instead of letting the
    # handler see `resume_command` and actually apply.
    state["resume_attempts"] = state.get("resume_attempts", 0) + 1
    state["resume_target_node"] = "S06.90"
    state = _advance(s06_runtime, "S06.90", state)

    # Not a plain `state["node_routes"]["S06.90"]` lookup: S06.90 genuinely
    # ran twice in this case (halt, then resume), and `_next_route_key`
    # (node_runtime.py) only reuses the plain `"S06.90"` key when a revisit
    # produces the *same* route as before -- a real value change (here,
    # "approval" -> "continue") claims a new `"S06.90#2"` slot instead, so
    # the plain key would still read the stale "approval" value. `route_for`
    # is the same lookup `add_routed_edge`/`_policy_outcome` themselves use.
    assert route_for("S06.90")(cast("OptimizationState", state)) == "continue"
    result = _model_from_ref(
        store, _ref_by_type(state, "RepositoryApplyResult"), RepositoryApplyResult
    )
    assert result.applied is True
    assert result.approval_decision == "approved"
    assert result.approval_actor_id == "owner-1"
    assert result.branch_name is not None
    branch_head = _git_output(repo, "rev-parse", result.branch_name)
    assert branch_head == result.commit_sha
