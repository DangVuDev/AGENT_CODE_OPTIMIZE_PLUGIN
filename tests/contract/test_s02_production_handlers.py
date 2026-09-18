from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from production_optimizer.application import NodePorts, build_s02_registrations, build_s02_runtime
from production_optimizer.application.node_runtime import NodeRuntime
from production_optimizer.contracts.a2 import RepositoryManifest, SourceSnapshot
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
)
from production_optimizer.contracts.s01 import SelectedSolution, SelectionApproval
from production_optimizer.contracts.s02 import ExecutionPlan, PlanQualityReport, TaskList
from production_optimizer.orchestration.catalog import S02_NODE_IDS

_TENANT = "TENANT-S02"
_CASE_ID = "OPT-S02-1"
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


_SCOPE_LINE = re.compile(r"^- (\S+) \(file\)", re.MULTILINE)
_CRITERION_LINE = re.compile(r"^- (\S+) \(improves", re.MULTILINE)


class _ScriptedPlanProvider:
    """Fake `ModelProviderPort` grounded in the real context it is given --
    extracts the real scope file and criterion id out of the prompt (never
    invents its own), mirroring `test_a3_production_handlers._ScriptedModelProvider`.
    """

    def __init__(
        self,
        *,
        cover_criterion: bool = True,
        bad_phase_order: bool = False,
        forced_scope_file: str | None = None,
        critic_payload: dict[str, Any] | None = None,
        forced_phase_risk_tier: str | None = None,
    ) -> None:
        self.calls: list[ModelCompletionRequest] = []
        self._cover_criterion = cover_criterion
        self._bad_phase_order = bad_phase_order
        self._forced_scope_file = forced_scope_file
        self._critic_payload = critic_payload
        self._forced_phase_risk_tier = forced_phase_risk_tier

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.calls.append(request)
        context = "\n".join(message.content for message in request.messages)
        if ":S02.30:" in request.idempotency_key:
            payload: dict[str, Any] | None = self._plan_payload(context)
        elif ":S02.80:" in request.idempotency_key:
            payload = self._critic_payload or {"omissions": [], "concerns": [], "approved": True}
        else:
            payload = None
        return ModelCompletionResult(
            request_id=f"req-{len(self.calls)}",
            model_id=request.model_id,
            model_version="scripted-1",
            raw_text=str(payload) if payload is not None else "",
            parsed_json=payload,
            valid_json=payload is not None,
            input_tokens=100,
            output_tokens=50,
            stop_reason="end_turn" if payload is not None else "refusal",
        )

    def healthcheck(self) -> bool:
        return True

    def _plan_payload(self, context: str) -> dict[str, Any]:
        scope_match = _SCOPE_LINE.search(context)
        scope_file = self._forced_scope_file or (
            scope_match.group(1) if scope_match else "src/app.py"
        )
        criterion_match = _CRITERION_LINE.search(context)
        criterion_id = criterion_match.group(1) if criterion_match else "latency-p95"
        done_criteria = [f"{criterion_id} improves"] if self._cover_criterion else ["done"]
        phases = [
            {
                "phase_id": "phase-1",
                "sequence": 1,
                "phase_kind": "implementation",
                **(
                    {"risk_tier": self._forced_phase_risk_tier}
                    if self._forced_phase_risk_tier is not None
                    else {}
                ),
                "treatment": {"variable": "threshold", "before": "100", "after": "50"},
                "done_criteria": done_criteria,
                "rollback_command": f"git checkout -- {scope_file}",
                "rollback_trigger": "latency regresses past target",
                "rollback_deadline_seconds": 600,
            }
        ]
        if self._bad_phase_order:
            # A diagnostic (cheap, reversible) phase sequenced *after* the
            # implementation (expensive) phase above -- exactly the
            # cheap-before-expensive violation `phase_ordering_by_risk`
            # must reject.
            phases.append(
                {
                    "phase_id": "phase-2",
                    "sequence": 2,
                    "phase_kind": "diagnostic",
                    "treatment": {
                        "variable": "threshold",
                        "before": "50",
                        "after": "confirmed",
                    },
                    "done_criteria": done_criteria,
                    "rollback_command": None,
                    "rollback_trigger": "diagnosis contradicts the treatment",
                    "rollback_deadline_seconds": 300,
                }
            )
        return {
            "phases": phases,
            "tasks": [
                {
                    "task_id": "task-1",
                    "phase_id": "phase-1",
                    "objective": "Lower the hot-path threshold",
                    "files": [scope_file],
                    "symbols": [],
                    "depends_on": [],
                    "proposed_creation": False,
                    "instructions": f"Edit {scope_file} to lower the threshold to 50",
                    "owner": "checkout-team",
                }
            ],
        }


def _ports(store: _MemoryArtifactStore, *, model: Any = None) -> NodePorts:
    return NodePorts(
        artifacts=store, intents=_MemoryIntentLedger(), model=model, model_id="scripted-model"
    )


def _advance(runtime: NodeRuntime, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    result = runtime.execute(node_id, state)  # type: ignore[arg-type]
    existing_refs = cast("list[ArtifactRef]", state.get("artifact_refs", []))
    new_refs = cast("list[ArtifactRef]", result.get("artifact_refs", []))
    merged = dict(state)
    merged.update(result)
    merged["artifact_refs"] = [*existing_refs, *new_refs]
    # `s02_revision_attempts` is a plain last-value field (see
    # contracts/state.py's docstring -- the writer already computes the new
    # total itself), so `merged.update(result)` above applies it correctly
    # with no special-casing needed.
    # `node_routes` is a merge-guarded state field too (`merge_node_routes`,
    # contracts/state.py): a real graph invocation accumulates every node's
    # route across the whole run, but this harness's plain `dict.update`
    # above replaces the *entire* dict with just this one node's single-entry
    # result. That silently loses S02.81's own prior-pass entries -- exactly
    # the history `node_runtime._next_route_key`/`_latest_route_key` need to
    # tell a repeat visit from a first one -- so union it in properly instead.
    merged["node_routes"] = {**state.get("node_routes", {}), **result.get("node_routes", {})}
    return merged


def _current_route(state: dict[str, Any], node_id: str) -> str:
    """Mirrors `node_runtime.route_for`'s `_latest_route_key` lookup: S02.81
    can be visited more than once per case, so its most recent route lives
    under `"S02.81"`, `"S02.81#2"`, `"S02.81#3"`, ... -- never a stale,
    overwritten bare key."""

    routes = state["node_routes"]
    if node_id not in routes:
        raise AssertionError(f"{node_id} never ran")
    key = node_id
    n = 2
    while f"{node_id}#{n}" in routes:
        key = f"{node_id}#{n}"
        n += 1
    return routes[key]


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


def _seed_case(
    tmp_path: Any,
    store: _MemoryArtifactStore,
    *,
    risk_ceiling: str,
    scope_file: str = "src/app.py",
) -> list[ArtifactRef]:
    repo = tmp_path / "repo"
    source_path = repo / scope_file
    source_path.parent.mkdir(parents=True)
    source_path.write_text("def handle():\n    threshold = 100\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_handle():\n    assert True\n")

    snapshot = SourceSnapshot(
        **_base_envelope_kwargs("SourceSnapshot"),
        repository_id="checkout-repo",
        canonical_path_ref=str(repo),
        git_revision=None,
        dirty=False,
        files=[],
    )
    snapshot_ref = _seal_and_store(store, snapshot)

    manifest = RepositoryManifest(
        **_base_envelope_kwargs("RepositoryManifest"),
        languages={"python": 1.0},
        modules=["src"],
        manifest_files=[],
        test_roots=["tests"],
        commands=[],
        tool_coverage={},
    )
    manifest_ref = _seal_and_store(store, manifest)

    strategy = SolutionStrategy(
        strategy_id="strategy-good",
        finding_ids=["finding-1"],
        title="Strategy good",
        mechanism="Adjust the hot-path threshold to restore p95 latency",
        strategy_tradeoffs="Some tradeoff",
        phase_templates=[
            ExperimentPhaseTemplate(
                phase_id="phase-1",
                sequence=1,
                phase_kind="implementation",
                treatment=Treatment(variable="threshold", before="100", after="50"),
            )
        ],
        risk_ceiling=cast("Any", risk_ceiling),
        risk_assessment=RiskAssessment(
            assessment_id="risk-1",
            strategy_id="strategy-good",
            risk_tier=cast("Any", risk_ceiling),
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
                    criterion_id="latency-p95",
                    direction="improves",
                    confidence=0.9,
                    basis="forecast",
                )
            ],
        ),
        tradeoff_analysis=TradeoffAnalysis(
            analysis_id="tradeoff-1",
            strategy_id="strategy-good",
            pros=["restores latency"],
            cons=["needs follow-up"],
            effort="low",
            uncertainty=0.1,
        ),
        validation_plan=ValidationPlan(
            plan_id="validation-1",
            strategy_id="strategy-good",
            test_command_ids=["benchmark"],
            benchmark_protocol="rerun the latency benchmark",
            stop_conditions=["still slow"],
        ),
        rollback_plan=RollbackPlan(
            plan_id="rollback-1",
            strategy_id="strategy-good",
            mechanism="revert the commit",
            verification="rerun the benchmark",
            reversible=True,
        ),
        scope_resolution=ScopeResolutionReport(
            report_id="scope-1",
            strategy_id="strategy-good",
            entries=[ScopeResolutionEntry(path_or_symbol=scope_file, kind="file", exists=True)],
            fully_resolved=True,
        ),
        evidence_ids=["evidence-1"],
        eligible=True,
    )
    portfolio = SolutionPortfolio(
        **_base_envelope_kwargs("SolutionPortfolio"),
        finding_set_digest=_ZERO_DIGEST,
        quality_report_digest=_ZERO_DIGEST,
        strategies=[strategy],
    )
    portfolio_ref = _seal_and_store(store, portfolio)

    selected = SelectedSolution(
        **{
            **_base_envelope_kwargs("SelectedSolution"),
            # Pass-scoped to match `s01_handlers`'s real S01.90 output shape
            # (BR-01-005 pass-scoping) -- s02_handlers._selected_solution_ref
            # looks up exactly this artifact_id for pass 0 (a fresh case, no
            # REVERT yet, which is what every fixture here simulates).
            "artifact_id": f"{_CASE_ID}-S01.90-pass0-SelectedSolution",
        },
        ranking_result_digest=_ZERO_DIGEST,
        solution_portfolio_digest=portfolio_ref.content_digest,
        strategy_id="strategy-good",
        approval=SelectionApproval(decision="auto_selected", policy_version="s01-ranking-v1"),
    )
    selected_ref = _seal_and_store(store, selected)

    return [snapshot_ref, manifest_ref, portfolio_ref, selected_ref]


def _state(refs: list[ArtifactRef]) -> dict[str, Any]:
    return {
        "case_id": _CASE_ID,
        "thread_id": "THREAD-S02-1",
        "tenant_id": _TENANT,
        "lane": "manual",
        "artifact_refs": refs,
    }


def test_s02_registrations_cover_every_node() -> None:
    registrations = build_s02_registrations()
    assert set(registrations) == set(S02_NODE_IDS)


def test_s02_auto_approves_and_seals_a_real_plan_for_a_low_risk_strategy(tmp_path: Any) -> None:
    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="prompt")
    model = _ScriptedPlanProvider()
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    routes: dict[str, str] = {}
    for node_id in S02_NODE_IDS:
        state = _advance(runtime, node_id, state)
        routes[node_id] = _current_route(state, node_id)

    assert routes["S02.10"] == "continue"
    assert routes["S02.81"] == "continue"
    assert routes["S02.90"] == "continue"

    quality = _model_from_ref(store, _ref_by_type(state, "PlanQualityReport"), PlanQualityReport)
    assert quality.passed is True
    assert {result.dimension: result.passed for result in quality.results} == {
        "phases_well_formed": True,
        "tasks_well_formed": True,
        "dependency_dag_acyclic": True,
        "phase_ordering_by_risk": True,
        "risk_ladder_ordering": True,
        "paths_resolve": True,
        "no_silent_path_repairs": True,
        "criteria_coverage": True,
        "rollback_defined": True,
        "critic_approved": True,
    }

    plan = _model_from_ref(store, _ref_by_type(state, "ExecutionPlan"), ExecutionPlan)
    assert plan.phases[0].treatment.variable == "threshold"
    assert plan.phases[0].risk_tier == "prompt"
    assert plan.phases[0].affected_criteria == ["latency-p95"]
    assert plan.phases[0].validation_command_ids == ["benchmark"]
    task_list = _model_from_ref(store, _ref_by_type(state, "TaskList"), TaskList)
    assert task_list.tasks[0].files == ["src/app.py"]
    assert quality.execution_plan_digest == plan.content_digest
    assert quality.task_list_digest == task_list.content_digest


def test_s02_81_blocks_a_silently_repaired_hallucinated_path(tmp_path: Any) -> None:
    """S02.50's `_repair_task_file_paths` still normalizes a hallucinated
    path to the one real file whose basename/suffix uniquely matches it
    (`src/checkout/service.go` -> the real `internal/checkout/service.go`)
    -- but that repair must now be a real, visible, blocking quality
    finding rather than something `paths_resolve` alone silently accepts,
    since the model asserted a path that never actually existed. (Renamed
    and rewritten from `test_s02_repairs_unique_model_invented_path_to_
    real_repository_path`, which previously asserted the bug's own
    symptom: a silent repair reaching S02.90 as `continue`.)"""

    store = _MemoryArtifactStore()
    refs = _seed_case(
        tmp_path,
        store,
        risk_ceiling="prompt",
        scope_file="internal/checkout/service.go",
    )
    model = _ScriptedPlanProvider(forced_scope_file="src/checkout/service.go")
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    route = "revision"
    attempts = 0
    latest_quality_ref: ArtifactRef | None = None
    for node_id in ("S02.10", "S02.20"):
        state = _advance(runtime, node_id, state)
    while route == "revision" and attempts < 5:
        for node_id in ("S02.30", "S02.40", "S02.50", "S02.60", "S02.70", "S02.80"):
            state = _advance(runtime, node_id, state)
        result = runtime.execute("S02.81", state)  # type: ignore[arg-type]
        latest_quality_ref = next(
            ref for ref in result["artifact_refs"] if ref.artifact_type == "PlanQualityReport"
        )
        state = _advance(runtime, "S02.81", state)
        route = _current_route(state, "S02.81")
        attempts += 1

    assert route == "rejected"
    assert state["s02_plan_draft"]["path_repairs"] == [
        "task-1: src/checkout/service.go -> internal/checkout/service.go"
    ]
    assert latest_quality_ref is not None
    quality = _model_from_ref(store, latest_quality_ref, PlanQualityReport)
    assert quality.passed is False
    repair_result = next(
        result for result in quality.results if result.dimension == "no_silent_path_repairs"
    )
    assert repair_result.passed is False
    assert repair_result.detail is not None
    assert "src/checkout/service.go -> internal/checkout/service.go" in repair_result.detail
    assert _try_ref_by_type(state, "ExecutionPlan") is None


def test_s02_40_keeps_models_own_valid_risk_tier_even_if_it_differs_from_heuristic(
    tmp_path: Any,
) -> None:
    """A model that explicitly declares a valid `risk_tier` must have that
    declaration respected, even when it differs from S02.40's own
    keyword-heuristic guess (`strategy.risk_ceiling`, seeded here as
    "code") -- the model may legitimately know the phase is higher-risk
    than a crude substring match can infer."""

    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="code")
    model = _ScriptedPlanProvider(forced_phase_risk_tier="architecture")
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    for node_id in S02_NODE_IDS:
        state = _advance(runtime, node_id, state)

    plan = _model_from_ref(store, _ref_by_type(state, "ExecutionPlan"), ExecutionPlan)
    assert plan.phases[0].risk_tier == "architecture"
    repairs = state["s02_plan_draft"]["phase_metadata_repairs"]
    assert not any(repair.startswith("phase-1: risk_tier") for repair in repairs)


def test_s02_40_fills_risk_tier_only_when_model_omits_it(tmp_path: Any) -> None:
    """The heuristic fallback still applies -- but only when the model's
    own declaration is missing/invalid, not merely different."""

    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="code")
    model = _ScriptedPlanProvider()  # no risk_tier in the phase payload at all
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    for node_id in S02_NODE_IDS:
        state = _advance(runtime, node_id, state)

    plan = _model_from_ref(store, _ref_by_type(state, "ExecutionPlan"), ExecutionPlan)
    assert plan.phases[0].risk_tier == "code"
    repairs = state["s02_plan_draft"]["phase_metadata_repairs"]
    assert "phase-1: risk_tier missing/invalid (None) -> 'code'" in repairs


def test_s02_critic_explicit_rejection_is_never_auto_approved(
    tmp_path: Any,
) -> None:
    """The critic's own explicit `approved: False` must never be silently
    overridden to True just because none of its concerns match the fixed
    anchor-keyword vocabulary -- an explicit rejection stays a rejection
    even when the model's reasoning is phrased in a way this codebase's
    grounding check can't verify. (Previously this exact scenario was
    silently auto-approved -- the concern's wording, "race conditions",
    doesn't contain any phase_id/task_id/file/criterion_id substring or the
    fixed vocabulary, so `_grounded_critique` used to flip approved=False to
    True. It no longer does.)"""

    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="prompt")
    model = _ScriptedPlanProvider(
        critic_payload={
            "omissions": [],
            "concerns": [
                "Removing blocking synchronization in the checkout service could introduce "
                "race conditions."
            ],
            "approved": False,
        }
    )
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    route = "revision"
    attempts = 0
    latest_quality_ref: ArtifactRef | None = None
    for node_id in ("S02.10", "S02.20"):
        state = _advance(runtime, node_id, state)
    while route == "revision" and attempts < 5:
        for node_id in ("S02.30", "S02.40", "S02.50", "S02.60", "S02.70", "S02.80"):
            state = _advance(runtime, node_id, state)
        result = runtime.execute("S02.81", state)  # type: ignore[arg-type]
        latest_quality_ref = next(
            ref for ref in result["artifact_refs"] if ref.artifact_type == "PlanQualityReport"
        )
        state = _advance(runtime, "S02.81", state)
        route = _current_route(state, "S02.81")
        attempts += 1

    assert route == "rejected"
    assert state["s02_critique"]["approved"] is False
    assert state["s02_critique"]["concerns"] == []
    assert state["s02_critique"]["ignored_ungrounded"] == [
        "Removing blocking synchronization in the checkout service could introduce race conditions."
    ]
    assert latest_quality_ref is not None
    quality = _model_from_ref(store, latest_quality_ref, PlanQualityReport)
    critic_result = next(
        result for result in quality.results if result.dimension == "critic_approved"
    )
    assert critic_result.passed is False


def test_s02_critic_explicit_approval_with_ungrounded_chatter_still_passes(
    tmp_path: Any,
) -> None:
    """The inverse of the rejection case, and the real intent the old test
    (now split above) was originally meant to prove: speculative,
    ungrounded risk chatter from the critic must not by itself block a plan
    the critic actually approved."""

    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="prompt")
    model = _ScriptedPlanProvider(
        critic_payload={
            "omissions": [],
            "concerns": [
                "Removing blocking synchronization in the checkout service could introduce "
                "race conditions."
            ],
            "approved": True,
        }
    )
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    for node_id in S02_NODE_IDS:
        state = _advance(runtime, node_id, state)

    assert _current_route(state, "S02.81") == "continue"
    assert state["s02_critique"]["approved"] is True
    assert state["s02_critique"]["concerns"] == []
    assert state["s02_critique"]["ignored_ungrounded"] == [
        "Removing blocking synchronization in the checkout service could introduce race conditions."
    ]
    quality = _model_from_ref(store, _ref_by_type(state, "PlanQualityReport"), PlanQualityReport)
    critic_result = next(
        result for result in quality.results if result.dimension == "critic_approved"
    )
    assert critic_result.passed is True


def test_s02_requires_approval_for_code_risk_strategy(tmp_path: Any) -> None:
    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="code")
    model = _ScriptedPlanProvider()
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    for node_id in (
        "S02.10",
        "S02.20",
        "S02.30",
        "S02.40",
        "S02.50",
        "S02.60",
        "S02.70",
        "S02.80",
        "S02.81",
    ):
        state = _advance(runtime, node_id, state)
    state = _advance(runtime, "S02.90", state)

    assert _current_route(state, "S02.90") == "approval"
    interrupt = state["pending_interrupt"]
    assert interrupt is not None
    assert interrupt.stage == "S02.90"
    plan_ref = _ref_by_type(state, "ExecutionPlan")
    assert interrupt.artifact_digest == plan_ref.content_digest


def test_s02_81_rejects_a_plan_missing_criteria_coverage_after_bounded_redraft(
    tmp_path: Any,
) -> None:
    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="prompt")
    model = _ScriptedPlanProvider(cover_criterion=False)
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    for node_id in ("S02.10", "S02.20"):
        state = _advance(runtime, node_id, state)

    route = "revision"
    attempts = 0
    latest_quality_ref: ArtifactRef | None = None
    while route == "revision" and attempts < 5:
        for node_id in ("S02.30", "S02.40", "S02.50", "S02.60", "S02.70", "S02.80"):
            state = _advance(runtime, node_id, state)
        result = runtime.execute("S02.81", state)  # type: ignore[arg-type]
        latest_quality_ref = next(
            ref for ref in result["artifact_refs"] if ref.artifact_type == "PlanQualityReport"
        )
        state = _advance(runtime, "S02.81", state)
        route = _current_route(state, "S02.81")
        attempts += 1

    assert route == "rejected"
    assert latest_quality_ref is not None
    # `PlanQualityReport`'s artifact_id is pass-scoped (see
    # `s02_handlers._s02_81`'s comment), so the generic `_ref_by_type`
    # first-match lookup would return an earlier, already-superseded
    # attempt's report -- read back the specific ref this last attempt
    # itself returned instead.
    quality = _model_from_ref(store, latest_quality_ref, PlanQualityReport)
    assert quality.passed is False
    coverage_result = next(r for r in quality.results if r.dimension == "criteria_coverage")
    assert coverage_result.passed is False
    assert _try_ref_by_type(state, "ExecutionPlan") is None


def test_s02_81_rejects_a_plan_that_sequences_diagnostic_after_implementation(
    tmp_path: Any,
) -> None:
    """`phase_ordering_by_risk`: a diagnostic (cheap, reversible) phase must
    never sequence after an implementation (expensive) phase -- BR-02-006 /
    S02.40's "ordered by dependency and risk" contract."""

    store = _MemoryArtifactStore()
    refs = _seed_case(tmp_path, store, risk_ceiling="prompt")
    model = _ScriptedPlanProvider(bad_phase_order=True)
    runtime = build_s02_runtime(ports=_ports(store, model=model))
    state = _state(refs)

    route = "revision"
    attempts = 0
    latest_quality_ref: ArtifactRef | None = None
    for node_id in ("S02.10", "S02.20"):
        state = _advance(runtime, node_id, state)
    while route == "revision" and attempts < 5:
        for node_id in ("S02.30", "S02.40", "S02.50", "S02.60", "S02.70", "S02.80"):
            state = _advance(runtime, node_id, state)
        result = runtime.execute("S02.81", state)  # type: ignore[arg-type]
        latest_quality_ref = next(
            ref for ref in result["artifact_refs"] if ref.artifact_type == "PlanQualityReport"
        )
        state = _advance(runtime, "S02.81", state)
        route = _current_route(state, "S02.81")
        attempts += 1

    assert route == "rejected"
    assert latest_quality_ref is not None
    quality = _model_from_ref(store, latest_quality_ref, PlanQualityReport)
    assert quality.passed is False
    ordering_result = next(r for r in quality.results if r.dimension == "phase_ordering_by_risk")
    assert ordering_result.passed is False
    assert ordering_result.detail is not None
    assert "diagnostic" in ordering_result.detail
    assert _try_ref_by_type(state, "ExecutionPlan") is None
