from __future__ import annotations

import ast
import operator as operator_module
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a1 import Criterion, OptimizationRequest
from production_optimizer.contracts.a2 import (
    BaselineSnapshot,
    BranchEvidenceRefs,
    ComparabilityReport,
    EvidenceBundle,
    EvidenceQualityReport,
    FileIdentity,
    SourceSnapshot,
    TrustLevel,
    VerificationManifest,
)
from production_optimizer.contracts.a3 import (
    A3IntakeDecision,
    A3QualityReport,
    AnalyzerObservation,
    AnalyzerObservationBranch,
    CitationResolutionEntry,
    CitationResolutionReport,
    CitationResolutionReportSet,
    CriterionImpact,
    EvidenceCatalog,
    EvidenceCatalogEntry,
    Finding,
    FindingDraft,
    FindingDraftSet,
    FindingJudgement,
    FindingJudgementSet,
    FindingSet,
    ImpactAssessment,
    PrioritizedSignalSet,
    ProblemSignal,
    ProblemSignalSet,
    QualityGateResult,
    RevisionDirective,
    RiskAssessment,
    RollbackPlan,
    ScopeResolutionEntry,
    ScopeResolutionReport,
    SolutionPortfolio,
    SolutionStrategy,
    SolutionStrategySet,
    StrategyDraft,
    StrategyDraftSet,
    TradeoffAnalysis,
    ValidationPlan,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelMessage,
    ModelRole,
)
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="a3-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_DEFAULT_A3_MODEL_ID = "claude-sonnet-5"
_A3_PROMPT_VERSION = "a3-v1"
_MAX_REVISION_ATTEMPTS = 3
_LOOP_BODY_NODES = frozenset(
    {"A3.60", "A3.61", "A3.62", "A3.63", "A3.64", "A3.70", "A3.80", "A3.81", "A3.82"}
)
_EXTERNAL_JOB_NODES = frozenset({"A3.40", "A3.50", "A3.60"})


def _model_id(ports: NodePorts) -> str:
    """The model name to request on `ports.model`.

    `ports.model_id` must match whatever `ports.model` actually is (an
    Anthropic alias, a local Ollama tag, ...) -- callers that wire in a
    non-default provider are expected to set it. Falls back to
    `_DEFAULT_A3_MODEL_ID` only for callers that never set it (the default
    provider it names).
    """

    return ports.model_id or _DEFAULT_A3_MODEL_ID
_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "A3.81": {NodeRoute.CONTINUE.value, NodeRoute.REVISION.value, NodeRoute.REJECTED.value},
    "A3.82": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
}


def build_a3_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build a runtime with all 22 production A3 handlers registered.

    A3.40/A3.50/A3.60 require `ports.model` to be non-`None` (they raise
    `RuntimeError` at call time otherwise) — see
    `docs/adr/0002-model-provider-port.md`. A3.32 optionally uses
    `ports.registry` if present (falls back to an honest "no domain analyzer
    registered" result otherwise). The rest only need `artifacts`/`intents`.
    """

    return NodeRuntime(build_a3_registrations(), ports=ports)


def build_a3_registrations() -> dict[str, RegisteredNode]:
    return build_bound_a3_registrations()


def _spec(node_id: str) -> NodeSpec:
    routes = _ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    side_effect_class = (
        SideEffectClass.EXTERNAL_JOB
        if node_id in _EXTERNAL_JOB_NODES
        else SideEffectClass.IDEMPOTENT_WRITE
    )
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-1-a3",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="a3-production-v1",
        side_effect_class=side_effect_class,
        timeout_seconds=120,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/implementation/05-lane-1-detailed-implementation-playbook.md",
        slo="A3 node completes within 120 seconds excluding model provider latency",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production A3 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"a3_{node_id.replace('.', '_')}"
    return execute


def build_bound_a3_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import A3_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in A3_NODE_IDS
    }


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "A3.10":
            return _a3_10(state, ports)
        case "A3.11":
            return _a3_11(state, ports)
        case "A3.20":
            return _a3_20(state, ports)
        case "A3.21":
            return _a3_21(state, ports)
        case "A3.30":
            return _a3_30(state, ports)
        case "A3.31":
            return _a3_31(state, ports)
        case "A3.32":
            return _a3_32(state, ports)
        case "A3.33":
            return _a3_33(state, ports)
        case "A3.40":
            return _a3_40(state, ports)
        case "A3.41":
            return _a3_41(state, ports)
        case "A3.50":
            return _a3_50(state, ports)
        case "A3.51":
            return _a3_51(state, ports)
        case "A3.60":
            return _a3_60(state, ports)
        case "A3.61":
            return _a3_61(state, ports)
        case "A3.62":
            return _a3_62(state, ports)
        case "A3.63":
            return _a3_63(state, ports)
        case "A3.64":
            return _a3_64(state, ports)
        case "A3.70":
            return _a3_70(state, ports)
        case "A3.80":
            return _a3_80(state, ports)
        case "A3.81":
            return _a3_81(state, ports)
        case "A3.82":
            return _a3_82(state, ports)
        case "A3.90":
            return _a3_90(state, ports)
        case _:
            return NodeExecution()


# ---------------------------------------------------------------------------
# A3.10 / A3.11 — intake verification and evidence indexing
# ---------------------------------------------------------------------------


def _a3_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    quality = _read_model(
        ports, state, _require_ref(state, "EvidenceQualityReport"), EvidenceQualityReport
    )
    comparability = _read_model(
        ports, state, _require_ref(state, "ComparabilityReport"), ComparabilityReport
    )
    baseline = _read_model(ports, state, _require_ref(state, "BaselineSnapshot"), BaselineSnapshot)

    mismatches: list[str] = []
    if bundle.baseline_digest != snapshot.content_digest:
        mismatches.append("EvidenceBundle.baseline_digest does not match SourceSnapshot digest")
    if baseline.source_snapshot_digest != snapshot.content_digest:
        mismatches.append(
            "BaselineSnapshot.source_snapshot_digest does not match SourceSnapshot digest"
        )
    if baseline.request_digest != request.content_digest:
        mismatches.append(
            "BaselineSnapshot.request_digest does not match OptimizationRequest digest"
        )

    decision = _seal(
        A3IntakeDecision(
            **_base_envelope(
                state,
                "A3IntakeDecision",
                parents=[request.content_digest, bundle.content_digest, baseline.content_digest],
            ),
            verified=not mismatches,
            mismatches=mismatches,
            gates_passed=quality.passed and comparability.comparable,
        )
    )
    ref = _put_envelope(ports, state, decision, node_id="A3.10")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_11(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)

    entries: list[EvidenceCatalogEntry] = []
    by_metric: dict[str, list[str]] = {}
    for item in bundle.evidence:
        entries.append(
            EvidenceCatalogEntry(
                evidence_id=item.evidence_id,
                metric_id=item.evidence_type,
                trust_level=item.trust_level,
                observed_at=item.identity.observed_at,
            )
        )
        by_metric.setdefault(item.evidence_type, []).append(item.evidence_id)

    catalog = _seal(
        EvidenceCatalog(
            **_base_envelope(state, "EvidenceCatalog", parents=[bundle.content_digest]),
            entries=entries,
            by_metric=by_metric,
        )
    )
    ref = _put_envelope(ports, state, catalog, node_id="A3.11")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# A3.20 / A3.21 — problem detection and priority scoring
# ---------------------------------------------------------------------------

_GUARDRAIL_OPERATORS: dict[str, Any] = {
    "lt": operator_module.lt,
    "lte": operator_module.le,
    "eq": operator_module.eq,
    "gte": operator_module.ge,
    "gt": operator_module.gt,
}


def _criterion_breached(criterion: Criterion, observed: float) -> bool:
    if criterion.direction == "minimize":
        return observed > criterion.target
    if criterion.direction == "maximize":
        return observed < criterion.target
    return observed != criterion.target


def _a3_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Compare `BaselineSnapshot` against criteria/guardrails.

    Today's A2 collectors only ever produce `metric_id`s like
    `unit_command_result`/`lint_command_result`/`type_command_result`/
    `source_map` (see `a2_handlers.py`'s `_EVIDENCE_TYPE_SOURCE_TYPE`) — real
    command exit codes and a file-map count, never an arbitrary business
    metric like `p95_latency_ms` (A2.62/A2.63 always report
    `unavailable_reason`). A `Criterion` naming such a metric legitimately
    finds zero matching aggregate here and produces no signal for it — an
    honest `NO_ACTIONABLE_PROBLEM` outcome, not a bug. Guardrails keyed to
    A2's real command metrics (e.g. a correctness guardrail on
    `unit_command_result`) fire correctly.
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    baseline = _read_model(ports, state, _require_ref(state, "BaselineSnapshot"), BaselineSnapshot)
    catalog = _read_model(ports, state, _require_ref(state, "EvidenceCatalog"), EvidenceCatalog)

    aggregates_by_metric = {agg.metric_id: agg for agg in baseline.aggregates}
    now = datetime.now(UTC)
    signals: list[ProblemSignal] = []

    for criterion in request.criteria:
        agg = aggregates_by_metric.get(criterion.metric_id)
        if agg is None or not _criterion_breached(criterion, agg.mean):
            continue
        signals.append(
            ProblemSignal(
                signal_id=f"signal-{criterion.criterion_id}",
                criterion_id=criterion.criterion_id,
                metric_id=criterion.metric_id,
                signal_kind="threshold_breach",
                description=(
                    f"{criterion.metric_id} mean {agg.mean} breaches "
                    f"{criterion.direction} target {criterion.target}"
                ),
                baseline_value=agg.mean,
                target_value=criterion.target,
                unit=criterion.unit,
                evidence_ids=catalog.by_metric.get(criterion.metric_id) or list(agg.sample_ids),
                detected_at=now,
            )
        )

    for guardrail in request.guardrails:
        agg = aggregates_by_metric.get(guardrail.metric_id)
        if agg is None:
            continue
        op = _GUARDRAIL_OPERATORS[guardrail.operator]
        if op(agg.mean, guardrail.threshold):
            continue
        signals.append(
            ProblemSignal(
                signal_id=f"signal-guardrail-{guardrail.guardrail_id}",
                # ProblemSignal has no dedicated guardrail field; reusing
                # criterion_id to carry guardrail_id is a pragmatic choice,
                # not a claim the guardrail is a criterion.
                criterion_id=guardrail.guardrail_id,
                metric_id=guardrail.metric_id,
                signal_kind="threshold_breach",
                description=(
                    f"guardrail {guardrail.guardrail_id} violated: "
                    f"{guardrail.metric_id} mean {agg.mean} fails "
                    f"{guardrail.operator} {guardrail.threshold}"
                ),
                baseline_value=agg.mean,
                target_value=guardrail.threshold,
                unit=guardrail.unit,
                evidence_ids=catalog.by_metric.get(guardrail.metric_id) or list(agg.sample_ids),
                detected_at=now,
            )
        )

    signal_set = _seal(
        ProblemSignalSet(
            **_base_envelope(
                state, "ProblemSignalSet", parents=[baseline.content_digest, catalog.content_digest]
            ),
            signals=signals,
        )
    )
    ref = _put_envelope(ports, state, signal_set, node_id="A3.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_21(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Deterministic priority score: breach magnitude (70%) + evidence weight (30%).

    A fixed, documented v1 heuristic (`policy_versions={"a3": "production-
    v1"}` on the envelope) rather than a `PolicyPort` call — `PolicyDecision`
    only carries `allowed`/`reasons`, no numeric score shape, so it cannot
    express a ranking. All signals are retained regardless of score, per the
    playbook ("Retain nonselected signals").
    """

    signal_set = _read_model(
        ports, state, _require_ref(state, "ProblemSignalSet"), ProblemSignalSet
    )

    scores: dict[str, float] = {}
    for signal in signal_set.signals:
        deviation = abs(signal.baseline_value - signal.target_value)
        denom = abs(signal.target_value) if signal.target_value != 0 else 1.0
        magnitude = min(deviation / denom, 10.0)
        evidence_weight = min(len(signal.evidence_ids), 5) / 5.0
        scores[signal.signal_id] = round(magnitude * 0.7 + evidence_weight * 0.3, 4)

    prioritized = _seal(
        PrioritizedSignalSet(
            **_base_envelope(state, "PrioritizedSignalSet", parents=[signal_set.content_digest]),
            signals=signal_set.signals,
            priority_scores=scores,
        )
    )
    ref = _put_envelope(ports, state, prioritized, node_id="A3.21")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# A3.30-A3.33 — analyzer fan-out
# ---------------------------------------------------------------------------

_LONG_FUNCTION_LINES = 50


def _iter_parsed_python_files(
    snapshot: SourceSnapshot,
) -> tuple[list[tuple[FileIdentity, ast.AST]], list[str]]:
    root = Path(snapshot.canonical_path_ref)
    parsed: list[tuple[FileIdentity, ast.AST]] = []
    gaps: list[str] = []
    for file_identity in snapshot.files:
        if not file_identity.relative_path.endswith(".py"):
            continue
        try:
            source = (root / file_identity.relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=file_identity.relative_path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            gaps.append(f"{file_identity.relative_path}: {exc.__class__.__name__}")
            continue
        parsed.append((file_identity, tree))
    return parsed, gaps


def _a3_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )
    signal_ids = [s.signal_id for s in prioritized.signals]

    observations: list[AnalyzerObservation] = []
    coverage_gaps: list[str] = []
    if signal_ids:
        parsed, coverage_gaps = _iter_parsed_python_files(snapshot)
        for file_identity, tree in parsed:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    length = (node.end_lineno or node.lineno) - node.lineno
                    if length >= _LONG_FUNCTION_LINES:
                        observations.append(
                            AnalyzerObservation(
                                observation_id=(
                                    f"syntax-{file_identity.relative_path}-{node.name}-{node.lineno}"
                                ),
                                source="syntax",
                                analyzer="ast-pattern-scan",
                                analyzer_version="1.0.0",
                                problem_signal_ids=signal_ids,
                                files=[file_identity.relative_path],
                                symbols=[node.name],
                                description=(
                                    f"function {node.name!r} spans {length} lines "
                                    f"(>= {_LONG_FUNCTION_LINES})"
                                ),
                                polarity="negative",
                                coverage=1.0,
                                evidence_ids=[],
                            )
                        )
                elif isinstance(node, ast.ExceptHandler) and node.type is None:
                    observations.append(
                        AnalyzerObservation(
                            observation_id=f"syntax-{file_identity.relative_path}-bare-except-{node.lineno}",
                            source="syntax",
                            analyzer="ast-pattern-scan",
                            analyzer_version="1.0.0",
                            problem_signal_ids=signal_ids,
                            files=[file_identity.relative_path],
                            symbols=[],
                            description="bare except clause swallows all exceptions",
                            polarity="negative",
                            coverage=1.0,
                            evidence_ids=[],
                        )
                    )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.30",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.30",
            source="syntax",
            observations=observations,
            coverage_gaps=coverage_gaps,
            unavailable_reason=None if signal_ids else "no prioritized signal to scope analysis to",
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_31(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )
    signal_ids = [s.signal_id for s in prioritized.signals]

    observations: list[AnalyzerObservation] = []
    coverage_gaps: list[str] = []
    if signal_ids:
        parsed, coverage_gaps = _iter_parsed_python_files(snapshot)
        for file_identity, tree in parsed:
            calls: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
            if calls:
                observations.append(
                    AnalyzerObservation(
                        observation_id=f"semantic-{file_identity.relative_path}-call-graph",
                        source="semantic",
                        analyzer="ast-call-graph",
                        analyzer_version="1.0.0",
                        problem_signal_ids=signal_ids,
                        files=[file_identity.relative_path],
                        symbols=sorted(calls),
                        description=f"{len(calls)} distinct call target(s) referenced in this file",
                        polarity="positive",
                        coverage=1.0,
                        evidence_ids=[],
                    )
                )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.31",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.31",
            source="semantic",
            observations=observations,
            coverage_gaps=coverage_gaps,
            unavailable_reason=None if signal_ids else "no prioritized signal to scope analysis to",
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.31")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_32(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Always empty today — no execution path for a registered domain analyzer.

    `ports.registry` (`RegistryKind.ANALYZER`) can name a domain analyzer,
    but nothing here can invoke one yet. Honestly-empty, like A2.62/A2.63,
    not a stub.
    """

    from production_optimizer.contracts.registries import RegistryKind

    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )

    reason = "no domain analyzer registered"
    if ports.registry is not None:
        tenant_id = _required_state_str(state, "tenant_id")
        active = ports.registry.list_active(
            tenant_id=tenant_id, registry_kind=RegistryKind.ANALYZER, at=datetime.now(UTC)
        )
        if active:
            reason = (
                f"{len(active)} domain analyzer registration(s) exist but "
                "no execution path is wired"
            )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.32",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.32",
            source="domain",
            observations=[],
            coverage_gaps=[],
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.32")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_33(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Correlates runtime telemetry — always empty today since A2.63 is."""

    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )

    try:
        telemetry_ref = _require_stage_ref(state, "A2.63", "BranchEvidenceRefs")
        telemetry_branch = _read_model(ports, state, telemetry_ref, BranchEvidenceRefs)
    except ValueError:
        telemetry_branch = None

    reason = (
        telemetry_branch.unavailable_reason
        if telemetry_branch is not None
        else "no A2.63 telemetry branch found in state"
    )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.33",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.33",
            source="runtime",
            observations=[],
            coverage_gaps=[],
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.33")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# A3.40 / A3.41 — generate finding drafts, resolve citations
# ---------------------------------------------------------------------------

_FINDING_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "problem_signal_ids": {"type": "array", "items": {"type": "string"}},
                    "claim_type": {
                        "type": "string",
                        "enum": ["observation", "hypothesis", "verified_cause"],
                    },
                    "symptom": {"type": "string"},
                    "scope_files": {"type": "array", "items": {"type": "string"}},
                    "scope_symbols": {"type": "array", "items": {"type": "string"}},
                    "causal_claim": {"type": "string"},
                    "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "counterevidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "unknowns": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "finding_id",
                    "problem_signal_ids",
                    "claim_type",
                    "symptom",
                    "causal_claim",
                    "supporting_evidence_ids",
                    "confidence",
                ],
            },
        }
    },
    "required": ["findings"],
}


def _build_finding_context(
    prioritized: PrioritizedSignalSet,
    catalog: EvidenceCatalog,
    branches: list[AnalyzerObservationBranch],
) -> str:
    lines = ["Prioritized problem signals:"]
    for signal in prioritized.signals:
        score = prioritized.priority_scores.get(signal.signal_id, 0.0)
        lines.append(
            f"- {signal.signal_id} (score={score}): {signal.description} "
            f"[metric={signal.metric_id} baseline={signal.baseline_value} "
            f"target={signal.target_value}]"
        )
    lines.append("\nAnalyzer observations:")
    for branch in branches:
        if branch.unavailable_reason:
            lines.append(f"- [{branch.source}] unavailable: {branch.unavailable_reason}")
            continue
        for observation in branch.observations:
            lines.append(
                f"- [{branch.source}] {observation.description} "
                f"(files={observation.files}, symbols={observation.symbols})"
            )
    known_ids = sorted(entry.evidence_id for entry in catalog.entries)
    lines.append("\nAvailable evidence IDs (cite only these): " + ", ".join(known_ids))
    return "\n".join(lines)


def _generate_finding_drafts(
    ports: NodePorts, state: OptimizationState, context: str
) -> tuple[list[FindingDraft], list[str], int]:
    assert ports.model is not None
    case_id = _required_state_str(state, "case_id")
    system_prompt = (
        "You are the A3 finding generator for an evidence-grounded code "
        "optimization platform. Emit typed findings strictly grounded in the "
        "provided evidence IDs and analyzer observations. Every claim must "
        "cite at least one evidence ID from the allowed list. Never invent "
        "evidence IDs, files, or symbols not present in the context. State "
        "unknowns explicitly rather than guessing."
    )
    request = ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id=_model_id(ports),
        prompt_version=_A3_PROMPT_VERSION,
        messages=[
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(role="user", content=context),
        ],
        response_schema=_FINDING_DRAFT_SCHEMA,
        max_output_tokens=4000,
        idempotency_key=f"{case_id}:A3.40:{_A3_PROMPT_VERSION}",
    )
    result = ports.model.complete(request)
    tokens = result.input_tokens + result.output_tokens
    if not result.valid_json:
        repair = request.model_copy(
            update={
                "messages": [
                    *request.messages,
                    ModelMessage(
                        role="user",
                        content=(
                            "Your previous reply did not match the required JSON "
                            "schema. Reply again with only a valid tool call "
                            "matching the schema."
                        ),
                    ),
                ],
                "idempotency_key": f"{request.idempotency_key}:repair-1",
            }
        )
        result = ports.model.complete(repair)
        tokens += result.input_tokens + result.output_tokens

    if not result.valid_json or result.parsed_json is None:
        return (
            [],
            ["generator did not produce schema-valid output within the one-repair budget"],
            tokens,
        )

    drafts: list[FindingDraft] = []
    failures: list[str] = []
    for raw in result.parsed_json.get("findings", []):
        try:
            drafts.append(FindingDraft.model_validate(raw))
        except ValidationError as exc:
            failures.append(f"{raw.get('finding_id', '<unknown>')}: {exc}")
    return drafts, failures, tokens


def _a3_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("A3.40 requires ModelProviderPort wired into NodePorts")

    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )
    catalog = _read_model(ports, state, _require_ref(state, "EvidenceCatalog"), EvidenceCatalog)
    branches = [
        _read_model(
            ports,
            state,
            _require_stage_ref(state, node_id, "AnalyzerObservationBranch"),
            AnalyzerObservationBranch,
        )
        for node_id in ("A3.30", "A3.31", "A3.32", "A3.33")
    ]

    if not prioritized.signals:
        drafts, failures, tokens = [], [], 0
    else:
        context = _build_finding_context(prioritized, catalog, branches)
        drafts, failures, tokens = _generate_finding_drafts(ports, state, context)

    draft_set = _seal(
        FindingDraftSet(
            **_base_envelope(
                state,
                "FindingDraftSet",
                parents=[prioritized.content_digest, catalog.content_digest],
            ),
            drafts=drafts,
            generation_failures=failures,
        )
    )
    ref = _put_envelope(ports, state, draft_set, node_id="A3.40")
    return NodeExecution(updates={"artifact_refs": [ref], "a3_model_tokens_spent": tokens})


def _a3_41(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    draft_set = _read_model(ports, state, _require_ref(state, "FindingDraftSet"), FindingDraftSet)
    catalog = _read_model(ports, state, _require_ref(state, "EvidenceCatalog"), EvidenceCatalog)
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)

    known_ids = {entry.evidence_id for entry in catalog.entries}
    values_by_id = {item.evidence_id: item.value for item in bundle.evidence}

    reports: list[CitationResolutionReport] = []
    for draft in draft_set.drafts:
        entries: list[CitationResolutionEntry] = []
        for evidence_id in draft.supporting_evidence_ids:
            resolved = evidence_id in known_ids
            in_scope = resolved
            value = values_by_id.get(evidence_id)
            supports = resolved and isinstance(value, int | float) and value != 0
            if resolved and in_scope and supports:
                reason = None
            elif not resolved:
                reason = "evidence id not found in catalog"
            else:
                reason = "evidence value does not support the claim (zero/neutral outcome)"
            entries.append(
                CitationResolutionEntry(
                    evidence_id=evidence_id,
                    resolved=resolved,
                    in_scope=in_scope,
                    supports_statement=supports,
                    reason=reason,
                )
            )
        reports.append(
            CitationResolutionReport(
                report_id=f"citation-{draft.finding_id}",
                finding_id=draft.finding_id,
                entries=entries,
                all_resolved=all(
                    e.resolved and e.in_scope and e.supports_statement for e in entries
                ),
            )
        )

    report_set = _seal(
        CitationResolutionReportSet(
            **_base_envelope(
                state,
                "CitationResolutionReportSet",
                parents=[draft_set.content_digest, catalog.content_digest],
            ),
            reports=reports,
        )
    )
    ref = _put_envelope(ports, state, report_set, node_id="A3.41")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# A3.50 / A3.51 — independent judge, maturity assignment
# ---------------------------------------------------------------------------

_JUDGEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "finding_id": {"type": "string"},
        "verdict": {"type": "string", "enum": ["accept", "reject"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["finding_id", "verdict", "reasons"],
}


def _build_judge_context(draft: FindingDraft, citation: CitationResolutionReport | None) -> str:
    lines = [
        f"Finding {draft.finding_id} (claim_type={draft.claim_type}):",
        f"Symptom: {draft.symptom}",
        f"Causal claim: {draft.causal_claim}",
        f"Supporting evidence IDs: {draft.supporting_evidence_ids}",
        f"Counterevidence IDs: {draft.counterevidence_ids}",
        f"Confidence: {draft.confidence}",
    ]
    if citation is not None:
        lines.append(f"Citation resolution: all_resolved={citation.all_resolved}")
        for entry in citation.entries:
            lines.append(
                f"  - {entry.evidence_id}: resolved={entry.resolved} in_scope={entry.in_scope} "
                f"supports={entry.supports_statement} reason={entry.reason}"
            )
    else:
        lines.append("Citation resolution: no report found for this finding")
    return "\n".join(lines)


def _judge_finding(
    ports: NodePorts, state: OptimizationState, finding_id: str, context: str
) -> tuple[FindingJudgement | None, int]:
    assert ports.model is not None
    case_id = _required_state_str(state, "case_id")
    system_prompt = (
        "You are the independent A3 judge. You did not generate this finding "
        "and have no access to the generator's reasoning beyond what is shown "
        "here. Check the symptom, location, wording and internal "
        "contradictions. Accept only if the citation resolution fully "
        "supports the causal claim."
    )
    request = ModelCompletionRequest(
        role=ModelRole.JUDGE,
        model_id=_model_id(ports),
        prompt_version=_A3_PROMPT_VERSION,
        messages=[
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(role="user", content=context),
        ],
        response_schema=_JUDGEMENT_SCHEMA,
        max_output_tokens=1000,
        idempotency_key=f"{case_id}:A3.50:{finding_id}:{_A3_PROMPT_VERSION}",
    )
    result = ports.model.complete(request)
    tokens = result.input_tokens + result.output_tokens
    raw = result.parsed_json
    if not result.valid_json or raw is None or "verdict" not in raw:
        return None, tokens
    return (
        FindingJudgement(
            judge_id=f"judge-{finding_id}",
            finding_id=finding_id,
            verdict=raw["verdict"],
            reasons=raw.get("reasons") or ["no reason provided"],
            model_id=result.model_id,
            model_version=result.model_version,
        ),
        tokens,
    )


def _a3_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("A3.50 requires ModelProviderPort wired into NodePorts")

    draft_set = _read_model(ports, state, _require_ref(state, "FindingDraftSet"), FindingDraftSet)
    citations = _read_model(
        ports,
        state,
        _require_ref(state, "CitationResolutionReportSet"),
        CitationResolutionReportSet,
    )
    citations_by_finding = {r.finding_id: r for r in citations.reports}

    judgements: list[FindingJudgement] = []
    tokens_spent = 0
    for draft in draft_set.drafts:
        citation = citations_by_finding.get(draft.finding_id)
        context = _build_judge_context(draft, citation)
        judgement, spent = _judge_finding(ports, state, draft.finding_id, context)
        tokens_spent += spent
        if judgement is not None:
            judgements.append(judgement)

    judgement_set = _seal(
        FindingJudgementSet(
            **_base_envelope(
                state,
                "FindingJudgementSet",
                parents=[draft_set.content_digest, citations.content_digest],
            ),
            judgements=judgements,
        )
    )
    ref = _put_envelope(ports, state, judgement_set, node_id="A3.50")
    return NodeExecution(updates={"artifact_refs": [ref], "a3_model_tokens_spent": tokens_spent})


def _compute_trust_level(
    draft: FindingDraft,
    citation: CitationResolutionReport,
    judgement: FindingJudgement,
    bundle: EvidenceBundle,
) -> TrustLevel:
    """T4 (required for `verified_cause`) is never assigned here on purpose.

    T4 needs multi-sample corroboration across independent collectors;
    today's A2 pipeline collects one sample per command, so no finding this
    platform produces can honestly claim T4 yet. `_a3_51` demotes any
    generator claim of `verified_cause` to `hypothesis` accordingly — never
    trust the generator's own maturity self-assessment.
    """

    if judgement.verdict != "accept" or not citation.all_resolved:
        return TrustLevel.T1
    evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
    identities_complete = all(
        evidence_by_id[evidence_id].identity.collector
        and evidence_by_id[evidence_id].identity.sample_id
        for evidence_id in draft.supporting_evidence_ids
        if evidence_id in evidence_by_id
    )
    if not identities_complete:
        return TrustLevel.T2
    return TrustLevel.T3 if len(draft.supporting_evidence_ids) > 1 else TrustLevel.T2


def _a3_51(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    draft_set = _read_model(ports, state, _require_ref(state, "FindingDraftSet"), FindingDraftSet)
    citations = _read_model(
        ports,
        state,
        _require_ref(state, "CitationResolutionReportSet"),
        CitationResolutionReportSet,
    )
    judgements = _read_model(
        ports, state, _require_ref(state, "FindingJudgementSet"), FindingJudgementSet
    )
    bundle = _read_model(ports, state, _require_ref(state, "EvidenceBundle"), EvidenceBundle)
    branches = [
        _read_model(
            ports,
            state,
            _require_stage_ref(state, node_id, "AnalyzerObservationBranch"),
            AnalyzerObservationBranch,
        )
        for node_id in ("A3.30", "A3.31", "A3.32", "A3.33")
    ]

    citations_by_finding = {r.finding_id: r for r in citations.reports}
    judgement_by_finding = {j.finding_id: j for j in judgements.judgements}

    findings: list[Finding] = []
    for draft in draft_set.drafts:
        judgement = judgement_by_finding.get(draft.finding_id)
        citation = citations_by_finding.get(draft.finding_id)
        if judgement is None or citation is None or not citation.all_resolved:
            continue

        trust_level = _compute_trust_level(draft, citation, judgement, bundle)
        claim_type = draft.claim_type
        if claim_type == "verified_cause" and (
            judgement.verdict != "accept" or trust_level != TrustLevel.T4
        ):
            claim_type = "hypothesis"

        findings.append(
            Finding(
                finding_id=draft.finding_id,
                problem_signal_ids=draft.problem_signal_ids,
                claim_type=claim_type,
                symptom=draft.symptom,
                scope_files=draft.scope_files,
                scope_symbols=draft.scope_symbols,
                runtime_path=draft.runtime_path,
                causal_claim=draft.causal_claim,
                supporting_evidence_ids=draft.supporting_evidence_ids,
                counterevidence_ids=draft.counterevidence_ids,
                analyzer_coverage=draft.analyzer_coverage,
                confidence=draft.confidence,
                trust_level=trust_level,
                judgement=judgement,
                unknowns=draft.unknowns,
            )
        )

    if not findings:
        raise ValueError(
            "A3.51 has zero accepted findings (none judged 'accept' with fully "
            "resolved citations); FindingSet requires at least one and there is "
            "no conditional route out of this node in the compiled graph"
        )

    all_observations = [obs for branch in branches for obs in branch.observations]
    coverage_gaps = [gap for branch in branches for gap in branch.coverage_gaps]

    finding_set = _seal(
        FindingSet(
            **_base_envelope(
                state,
                "FindingSet",
                parents=[
                    bundle.content_digest,
                    draft_set.content_digest,
                    judgements.content_digest,
                ],
            ),
            evidence_bundle_digest=bundle.content_digest,
            findings=findings,
            observations=all_observations,
            citation_reports=citations.reports,
            coverage_gaps=coverage_gaps,
        )
    )
    ref = _put_envelope(ports, state, finding_set, node_id="A3.51")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# A3.60-A3.82 — strategy generation, assembly and the bounded revision loop
# ---------------------------------------------------------------------------


def _revision_pass(state: OptimizationState) -> int:
    return state.get("a3_revision_attempts", 0) or 0


def _pass_stage_id(node_id: str, pass_number: int) -> str:
    return f"{node_id}-pass{pass_number}"


def _latest_revision_directive(
    ports: NodePorts, state: OptimizationState
) -> RevisionDirective | None:
    candidates = [
        ref for ref in state.get("artifact_refs", []) if ref.artifact_type == "RevisionDirective"
    ]
    if not candidates:
        return None
    directives = [_read_model(ports, state, ref, RevisionDirective) for ref in candidates]
    return max(directives, key=lambda directive: directive.attempt_number)


def _solution_strategy_to_draft(strategy: SolutionStrategy) -> StrategyDraft:
    return StrategyDraft(
        strategy_id=strategy.strategy_id,
        finding_ids=strategy.finding_ids,
        title=strategy.title,
        mechanism=strategy.mechanism,
        strategy_tradeoffs=strategy.strategy_tradeoffs,
        phase_templates=strategy.phase_templates,
        risk_ceiling=strategy.risk_ceiling,
        evidence_ids=strategy.evidence_ids,
        assumptions=strategy.assumptions,
        target_paths=[entry.path_or_symbol for entry in strategy.scope_resolution.entries],
        impact_assessment=strategy.impact_assessment,
        tradeoff_analysis=strategy.tradeoff_analysis,
        validation_plan=strategy.validation_plan,
        rollback_plan=strategy.rollback_plan,
        scope_resolution=strategy.scope_resolution,
        risk_assessment=strategy.risk_assessment,
        eligible=strategy.eligible,
        gate_reasons=strategy.gate_reasons,
    )


_STRATEGY_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "strategies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "strategy_id": {"type": "string"},
                    "finding_ids": {"type": "array", "items": {"type": "string"}},
                    "title": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "strategy_tradeoffs": {"type": "string"},
                    "phase_templates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "phase_id": {"type": "string"},
                                "sequence": {"type": "integer"},
                                "phase_kind": {
                                    "type": "string",
                                    "enum": ["diagnostic", "implementation"],
                                },
                                "treatment": {
                                    "type": "object",
                                    "properties": {
                                        "variable": {"type": "string"},
                                        "before": {"type": "string"},
                                        "after": {"type": "string"},
                                    },
                                    "required": ["variable", "before", "after"],
                                },
                                "risk_factors": {"type": "array", "items": {"type": "string"}},
                                "expected_observations": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["phase_id", "sequence", "phase_kind", "treatment"],
                        },
                    },
                    "risk_ceiling": {
                        "type": "string",
                        "enum": ["experiment_config", "prompt", "code", "architecture"],
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "target_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "strategy_id",
                    "finding_ids",
                    "title",
                    "mechanism",
                    "strategy_tradeoffs",
                    "phase_templates",
                    "risk_ceiling",
                    "evidence_ids",
                ],
            },
        }
    },
    "required": ["strategies"],
}


def _build_strategy_context(findings: list[Finding]) -> str:
    lines = ["Findings eligible for strategy generation:"]
    for finding in findings:
        lines.append(
            f"- {finding.finding_id} ({finding.claim_type}, confidence={finding.confidence}): "
            f"{finding.causal_claim} [evidence={finding.supporting_evidence_ids}]"
        )
    return "\n".join(lines)


def _generate_strategy_drafts(
    ports: NodePorts, state: OptimizationState, context: str, *, attempt: int
) -> tuple[list[StrategyDraft], int]:
    assert ports.model is not None
    case_id = _required_state_str(state, "case_id")
    system_prompt = (
        "You are the A3 strategy generator. Propose materially different "
        "remediation strategies tied to the given findings. Hypothesis-only "
        "findings may only receive diagnostic phases, never implementation "
        "phases. Each phase must contain exactly one logical treatment."
    )
    request = ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id=_model_id(ports),
        prompt_version=_A3_PROMPT_VERSION,
        messages=[
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(role="user", content=context),
        ],
        response_schema=_STRATEGY_DRAFT_SCHEMA,
        max_output_tokens=6000,
        idempotency_key=f"{case_id}:A3.60:attempt{attempt}:{_A3_PROMPT_VERSION}",
    )
    result = ports.model.complete(request)
    tokens = result.input_tokens + result.output_tokens
    if not result.valid_json:
        repair = request.model_copy(
            update={
                "messages": [
                    *request.messages,
                    ModelMessage(
                        role="user",
                        content="Reply again with only a valid tool call matching the schema.",
                    ),
                ],
                "idempotency_key": f"{request.idempotency_key}:repair-1",
            }
        )
        result = ports.model.complete(repair)
        tokens += result.input_tokens + result.output_tokens

    if not result.valid_json or result.parsed_json is None:
        return [], tokens

    drafts: list[StrategyDraft] = []
    for raw in result.parsed_json.get("strategies", []):
        try:
            drafts.append(StrategyDraft.model_validate(raw))
        except ValidationError:
            continue
    return drafts, tokens


def _a3_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    if ports.model is None:
        raise RuntimeError("A3.60 requires ModelProviderPort wired into NodePorts")

    finding_set = _read_model(ports, state, _require_ref(state, "FindingSet"), FindingSet)
    current_pass = _revision_pass(state)
    directive = _latest_revision_directive(ports, state)

    if current_pass > 0 and directive is not None and directive.targeted_strategy_ids:
        eligible_findings = [
            f for f in finding_set.findings if f.finding_id in directive.targeted_finding_ids
        ]
        previous_ref = _require_stage_ref(
            state, _pass_stage_id("A3.80", current_pass - 1), "SolutionStrategySet"
        )
        previous = _read_model(ports, state, previous_ref, SolutionStrategySet)
        carried_forward = [
            _solution_strategy_to_draft(strategy)
            for strategy in previous.strategies
            if strategy.strategy_id not in directive.targeted_strategy_ids
        ]
    else:
        eligible_findings = finding_set.findings
        carried_forward = []

    context = _build_strategy_context(eligible_findings)
    new_drafts, tokens = _generate_strategy_drafts(ports, state, context, attempt=current_pass)

    draft_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.60", current_pass),
                "StrategyDraftSet",
                parents=[finding_set.content_digest],
            ),
            strategies=[*carried_forward, *new_drafts],
        )
    )
    ref = _put_envelope(ports, state, draft_set, node_id="A3.60")
    return NodeExecution(updates={"artifact_refs": [ref], "a3_model_tokens_spent": tokens})


def _a3_61(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.60", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        sequences = [phase.sequence for phase in draft.phase_templates]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            reasons.append("phase_templates sequence numbers are not strictly ordered/unique")
        eligible = draft.eligible and not reasons
        updated.append(draft.model_copy(update={"eligible": eligible, "gate_reasons": reasons}))

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.61", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.61")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_62(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Assigns a conservative `forecast` impact for every criterion.

    A3 never executes a strategy (non-negotiable: A3 does not edit source),
    so no criterion impact can honestly be `basis="measured"` at this stage.
    A real forecast (`direction`, `confidence`) needs either a richer
    generator schema or actual execution telemetry, neither of which exists
    yet — this is a deliberately conservative placeholder, not a claim of
    analysis depth this platform doesn't have.
    """

    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.61", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        if draft.impact_assessment is not None:
            updated.append(draft)
            continue
        impacts = [
            CriterionImpact(
                criterion_id=criterion.criterion_id,
                direction="unknown",
                confidence=0.0,
                basis="forecast",
            )
            for criterion in request.criteria
        ]
        assessment = ImpactAssessment(
            assessment_id=f"impact-{draft.strategy_id}",
            strategy_id=draft.strategy_id,
            criterion_impacts=impacts,
        )
        updated.append(draft.model_copy(update={"impact_assessment": assessment}))

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.62", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.62")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_63(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Deterministic tradeoff assembly + duplicate-strategy detection.

    Same limitation as A3.62: no execution has happened, so this derives a
    coarse `TradeoffAnalysis` from what the generator already supplied
    (`strategy_tradeoffs` text, phase count) rather than a real cost/benefit
    analysis. The one thing genuinely enforced deterministically is the
    playbook's duplicate-strategy exit test.
    """

    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.62", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)

    seen_tradeoffs: dict[str, str] = {}
    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        duplicate_of = seen_tradeoffs.get(draft.strategy_tradeoffs)
        if duplicate_of is not None:
            reasons.append(f"strategy_tradeoffs text is identical to {duplicate_of}")
        else:
            seen_tradeoffs[draft.strategy_tradeoffs] = draft.strategy_id

        tradeoff_analysis = draft.tradeoff_analysis
        if tradeoff_analysis is None:
            phase_count = len(draft.phase_templates)
            effort: Literal["low", "medium", "high"] = (
                "low" if phase_count <= 1 else "medium" if phase_count <= 3 else "high"
            )
            tradeoff_analysis = TradeoffAnalysis(
                analysis_id=f"tradeoff-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                pros=[draft.strategy_tradeoffs],
                cons=[f"unvalidated until executed: {draft.mechanism[:200]}"],
                prerequisites=[
                    p.phase_id for p in draft.phase_templates if p.phase_kind == "diagnostic"
                ],
                effort=effort,
                uncertainty=0.5,
                evidence_ids=draft.evidence_ids,
            )

        eligible = draft.eligible and not reasons
        updated.append(
            draft.model_copy(
                update={
                    "gate_reasons": reasons,
                    "eligible": eligible,
                    "tradeoff_analysis": tradeoff_analysis,
                }
            )
        )

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.63", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.63")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_64(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Binds validation to real A2.31 commands; rollback via `git revert`.

    Never invents a validation command (per playbook) — a strategy gets no
    `test_command_ids` unless `VerificationManifest` (A2.31) actually has
    repository-owned commands. `git revert <commit-sha>` is always real and
    executable for any single-commit change regardless of which files it
    touches; precise per-file scope isn't known until A3.70.
    """

    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.63", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)
    verification = _read_model(
        ports, state, _require_ref(state, "VerificationManifest"), VerificationManifest
    )
    real_command_ids = [command.command_id for command in verification.commands]

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        validation_plan = draft.validation_plan
        if validation_plan is None:
            if real_command_ids:
                validation_plan = ValidationPlan(
                    plan_id=f"validation-{draft.strategy_id}",
                    strategy_id=draft.strategy_id,
                    test_command_ids=real_command_ids,
                    benchmark_protocol="rerun repository-owned commands via A2.50 worker jobs",
                    expected_metric_movements={
                        cid: "unchanged-or-improved" for cid in real_command_ids
                    },
                    stop_conditions=["any previously-passing command starts failing"],
                )
            else:
                reasons.append(
                    "no repository-owned verification command exists to bind a validation plan to"
                )
                validation_plan = ValidationPlan(
                    plan_id=f"validation-{draft.strategy_id}",
                    strategy_id=draft.strategy_id,
                    test_command_ids=[],
                    benchmark_protocol="none available",
                    stop_conditions=["no verification command available"],
                )

        rollback_plan = draft.rollback_plan
        if rollback_plan is None:
            rollback_plan = RollbackPlan(
                plan_id=f"rollback-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                mechanism="git revert <commit-sha-of-this-change>",
                verification=(
                    "rerun validation_plan.test_command_ids and confirm baseline exit codes "
                    "are unchanged"
                ),
                reversible=True,
            )

        eligible = draft.eligible and not reasons
        updated.append(
            draft.model_copy(
                update={
                    "validation_plan": validation_plan,
                    "rollback_plan": rollback_plan,
                    "gate_reasons": reasons,
                    "eligible": eligible,
                }
            )
        )

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.64", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest, verification.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.64")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.64", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    known_paths = {f.relative_path for f in snapshot.files}

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        scope_resolution = draft.scope_resolution
        if scope_resolution is None:
            if draft.target_paths:
                entries = [
                    ScopeResolutionEntry(
                        path_or_symbol=path,
                        kind="file",
                        exists=path in known_paths,
                        proposed_creation=path not in known_paths,
                    )
                    for path in draft.target_paths
                ]
            else:
                entries = [
                    ScopeResolutionEntry(
                        path_or_symbol="<unspecified>",
                        kind="file",
                        exists=False,
                        proposed_creation=True,
                    )
                ]
            scope_resolution = ScopeResolutionReport(
                report_id=f"scope-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                entries=entries,
                fully_resolved=all(entry.exists or entry.proposed_creation for entry in entries),
            )
        eligible = draft.eligible and not reasons
        updated.append(
            draft.model_copy(
                update={
                    "scope_resolution": scope_resolution,
                    "gate_reasons": reasons,
                    "eligible": eligible,
                }
            )
        )

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.70", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest, snapshot.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.70")
    return NodeExecution(updates={"artifact_refs": [ref]})


_RISK_SENSITIVE_KEYWORDS = ("migrations/", "auth", "secrets", "security")


def _a3_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.70", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)

    strategies: list[SolutionStrategy] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)

        missing = [
            name
            for name, value in (
                ("impact_assessment", draft.impact_assessment),
                ("tradeoff_analysis", draft.tradeoff_analysis),
                ("validation_plan", draft.validation_plan),
                ("rollback_plan", draft.rollback_plan),
                ("scope_resolution", draft.scope_resolution),
            )
            if value is None
        ]
        if missing:
            # Should not happen given the pipeline's own sequencing (every
            # prior stage unconditionally fills its assigned field); this is
            # a defensive guard against a stage being skipped, not a normal
            # code path. A SolutionStrategy cannot be constructed without
            # every required nested field, so such a draft is dropped rather
            # than fabricated.
            continue

        assert draft.scope_resolution is not None
        assert draft.tradeoff_analysis is not None
        assert draft.rollback_plan is not None
        assert draft.impact_assessment is not None
        assert draft.validation_plan is not None

        risk_assessment = draft.risk_assessment
        if risk_assessment is None:
            paths = [entry.path_or_symbol for entry in draft.scope_resolution.entries]
            sensitive = any(
                keyword in path.lower() for path in paths for keyword in _RISK_SENSITIVE_KEYWORDS
            )
            risk_assessment = RiskAssessment(
                assessment_id=f"risk-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                risk_tier=draft.risk_ceiling,
                blast_radius=f"{len(paths)} path(s): {paths}" if paths else "unspecified scope",
                reversibility="fast" if draft.rollback_plan.reversible else "slow",
                uncertainty=draft.tradeoff_analysis.uncertainty,
                migration_impact=sensitive,
                security_impact=sensitive,
                factors=["scope touches a sensitive path"] if sensitive else [],
            )

        if not draft.scope_resolution.fully_resolved:
            reasons.append("scope_resolution is not fully resolved")

        eligible = draft.eligible and not reasons

        strategies.append(
            SolutionStrategy(
                strategy_id=draft.strategy_id,
                finding_ids=draft.finding_ids,
                title=draft.title,
                mechanism=draft.mechanism,
                strategy_tradeoffs=draft.strategy_tradeoffs,
                phase_templates=draft.phase_templates,
                risk_ceiling=draft.risk_ceiling,
                risk_assessment=risk_assessment,
                impact_assessment=draft.impact_assessment,
                tradeoff_analysis=draft.tradeoff_analysis,
                validation_plan=draft.validation_plan,
                rollback_plan=draft.rollback_plan,
                scope_resolution=draft.scope_resolution,
                evidence_ids=draft.evidence_ids,
                assumptions=draft.assumptions,
                eligible=eligible,
                gate_reasons=reasons,
            )
        )

    strategy_set = _seal(
        SolutionStrategySet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.80", current_pass),
                "SolutionStrategySet",
                parents=[draft_set.content_digest],
            ),
            strategies=strategies,
        )
    )
    ref = _put_envelope(ports, state, strategy_set, node_id="A3.80")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _a3_81(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Deterministic quality gate; routes `continue`/`revision`/`rejected`.

    `A3QualityReport.solution_portfolio_digest` digests the *candidate*
    `SolutionStrategySet` this node evaluated, not the final sealed
    `SolutionPortfolio` — that artifact does not exist until A3.90 runs,
    strictly after this node, so it cannot be referenced here without a
    circular dependency between the two envelopes' required digest fields.
    """

    current_pass = _revision_pass(state)
    finding_set = _read_model(ports, state, _require_ref(state, "FindingSet"), FindingSet)
    strategy_ref = _require_stage_ref(
        state, _pass_stage_id("A3.80", current_pass), "SolutionStrategySet"
    )
    strategy_set = _read_model(ports, state, strategy_ref, SolutionStrategySet)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )

    finding_gate_results = [
        QualityGateResult(
            dimension="finding_has_evidence",
            passed=bool(finding.supporting_evidence_ids),
            detail=finding.finding_id,
        )
        for finding in finding_set.findings
    ]
    strategy_gate_results = [
        QualityGateResult(
            dimension="strategy_eligible",
            passed=strategy.eligible,
            detail=(
                f"{strategy.strategy_id}: "
                f"{'; '.join(strategy.gate_reasons) if strategy.gate_reasons else 'ok'}"
            ),
        )
        for strategy in strategy_set.strategies
    ]
    cause_maturity_gate_results = [
        QualityGateResult(
            dimension="cause_maturity",
            passed=(finding.claim_type != "verified_cause" or finding.trust_level == TrustLevel.T4),
            detail=finding.finding_id,
        )
        for finding in finding_set.findings
    ]

    eligible_strategies = [strategy for strategy in strategy_set.strategies if strategy.eligible]
    distinct_tradeoffs = len({strategy.strategy_tradeoffs for strategy in eligible_strategies})
    portfolio_gate_results = [
        QualityGateResult(
            dimension="at_least_one_eligible_strategy",
            passed=len(eligible_strategies) >= 1,
            detail=f"{len(eligible_strategies)} eligible of {len(strategy_set.strategies)}",
        ),
        QualityGateResult(
            dimension="strategies_materially_different",
            passed=distinct_tradeoffs == len(eligible_strategies),
            detail="ok"
            if distinct_tradeoffs == len(eligible_strategies)
            else "duplicate strategy_tradeoffs text",
        ),
    ]

    passed = all(
        result.passed
        for result in (
            *finding_gate_results,
            *strategy_gate_results,
            *cause_maturity_gate_results,
            *portfolio_gate_results,
        )
    )

    report = _seal(
        A3QualityReport(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.81", current_pass),
                "A3QualityReport",
                parents=[finding_set.content_digest, strategy_set.content_digest],
            ),
            finding_set_digest=finding_set.content_digest,
            solution_portfolio_digest=strategy_set.content_digest,
            passed=passed,
            finding_gate_results=finding_gate_results,
            strategy_gate_results=strategy_gate_results,
            cause_maturity_gate_results=cause_maturity_gate_results,
            portfolio_gate_results=portfolio_gate_results,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="A3.81")

    tokens_so_far = state.get("a3_model_tokens_spent", 0)
    budget_exhausted = (
        current_pass >= _MAX_REVISION_ATTEMPTS
        or tokens_so_far >= request.budget.maximum_model_tokens
    )

    if passed:
        route = NodeRoute.CONTINUE
    elif budget_exhausted:
        route = NodeRoute.REJECTED
    else:
        route = NodeRoute.REVISION

    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


def _a3_82(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Targets only rejected strategies/findings for regeneration.

    Accepted artifacts are reused unchanged on the next pass, per the
    playbook ("A3.82 may target only rejected findings/strategies").
    """

    current_pass = _revision_pass(state)
    strategy_ref = _require_stage_ref(
        state, _pass_stage_id("A3.80", current_pass), "SolutionStrategySet"
    )
    strategy_set = _read_model(ports, state, strategy_ref, SolutionStrategySet)
    quality_ref = _require_stage_ref(
        state, _pass_stage_id("A3.81", current_pass), "A3QualityReport"
    )
    quality = _read_model(ports, state, quality_ref, A3QualityReport)
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )

    next_attempt = current_pass + 1
    tokens_so_far = state.get("a3_model_tokens_spent", 0)
    budget_exhausted = (
        next_attempt >= _MAX_REVISION_ATTEMPTS
        or tokens_so_far >= request.budget.maximum_model_tokens
    )

    targeted_strategy_ids = [s.strategy_id for s in strategy_set.strategies if not s.eligible]
    targeted_finding_ids = sorted(
        {
            finding_id
            for s in strategy_set.strategies
            if not s.eligible
            for finding_id in s.finding_ids
        }
    )

    if budget_exhausted or not targeted_strategy_ids:
        reason = (
            "revision/token budget exhausted"
            if budget_exhausted
            else "quality gate failed with no targetable ineligible strategy"
        )
        directive = _seal(
            RevisionDirective(
                **_stage_envelope(
                    state,
                    _pass_stage_id("A3.82", current_pass),
                    "RevisionDirective",
                    parents=[quality.content_digest],
                ),
                attempt_number=next_attempt,
                targeted_finding_ids=[],
                targeted_strategy_ids=[],
                reason=reason,
            )
        )
        ref = _put_envelope(ports, state, directive, node_id="A3.82")
        return NodeExecution(
            route=NodeRoute.REJECTED, updates={"artifact_refs": [ref], "a3_revision_attempts": 1}
        )

    directive = _seal(
        RevisionDirective(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.82", current_pass),
                "RevisionDirective",
                parents=[quality.content_digest],
            ),
            attempt_number=next_attempt,
            targeted_finding_ids=targeted_finding_ids,
            targeted_strategy_ids=targeted_strategy_ids,
            reason="targeted revision of ineligible strategies",
        )
    )
    ref = _put_envelope(ports, state, directive, node_id="A3.82")
    return NodeExecution(
        route=NodeRoute.CONTINUE, updates={"artifact_refs": [ref], "a3_revision_attempts": 1}
    )


def _a3_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    current_pass = _revision_pass(state)
    finding_set = _read_model(ports, state, _require_ref(state, "FindingSet"), FindingSet)
    strategy_ref = _require_stage_ref(
        state, _pass_stage_id("A3.80", current_pass), "SolutionStrategySet"
    )
    strategy_set = _read_model(ports, state, strategy_ref, SolutionStrategySet)
    quality_ref = _require_stage_ref(
        state, _pass_stage_id("A3.81", current_pass), "A3QualityReport"
    )
    quality = _read_model(ports, state, quality_ref, A3QualityReport)

    portfolio = _seal(
        SolutionPortfolio(
            **_base_envelope(
                state,
                "SolutionPortfolio",
                parents=[
                    finding_set.content_digest,
                    strategy_set.content_digest,
                    quality.content_digest,
                ],
            ),
            finding_set_digest=finding_set.content_digest,
            strategies=strategy_set.strategies,
            quality_report_digest=quality.content_digest,
        )
    )
    ref = _put_envelope(ports, state, portfolio, node_id="A3.90")
    return NodeExecution(updates={"artifact_refs": [ref], "solution_portfolio_ref": ref})


# ---------------------------------------------------------------------------
# Shared envelope/artifact helpers (self-contained, mirrors a1_handlers.py /
# a2_handlers.py rather than importing across lanes)
# ---------------------------------------------------------------------------


def _put_envelope(
    ports: NodePorts, state: OptimizationState, envelope: ArtifactEnvelope, *, node_id: str
) -> ArtifactRef:
    content = canonical_json(envelope.model_dump(mode="json", exclude={"content_digest"}))
    generic_ref = ports.artifacts.put_json(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=envelope.content_digest,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:{node_id}:"
            f"{envelope.artifact_type}:{envelope.artifact_id}"
        ),
    )
    return ArtifactRef(
        artifact_type=envelope.artifact_type,
        schema_version=envelope.schema_version,
        artifact_id=envelope.artifact_id,
        content_digest=envelope.content_digest,
        uri=generic_ref.uri,
    )


def _read_model[T: BaseModel](
    ports: NodePorts, state: OptimizationState, ref: ArtifactRef, model: type[T]
) -> T:
    content = ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=ref)
    raw = TypeAdapter(dict[str, Any]).validate_json(content)
    if issubclass(model, ArtifactEnvelope):
        raw.setdefault("content_digest", ref.content_digest)
    return model.model_validate(raw)


def _seal[T: ArtifactEnvelope](model: T) -> T:
    return model.model_copy(update={"content_digest": model_content_digest(model)})


def _base_envelope(
    state: OptimizationState, artifact_type: str, *, parents: list[str] | None = None
) -> dict[str, Any]:
    return {
        "artifact_id": f"{_required_state_str(state, 'case_id')}-{artifact_type}",
        "tenant_id": _required_state_str(state, "tenant_id"),
        "case_id": _required_state_str(state, "case_id"),
        "created_at": datetime.now(UTC),
        "producer": _PRODUCER,
        "policy_versions": {"a3": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _stage_artifact_id(case_id: str, node_id: str, artifact_type: str) -> str:
    return f"{case_id}-{node_id}-{artifact_type}"


def _stage_envelope(
    state: OptimizationState, node_id: str, artifact_type: str, *, parents: list[str]
) -> dict[str, Any]:
    """`_base_envelope` with a node-scoped `artifact_id`.

    Needed whenever more than one node can produce the same
    `artifact_type` — the A3.30-A3.33 fan-out (like A2's
    `BranchEvidenceRefs`) and the A3.60-A3.82 revision loop, where the same
    node can also fire more than once (across passes; see
    `_pass_stage_id`). The default `_base_envelope` artifact_id
    (`f"{case_id}-{artifact_type}"`) would collide across producers/passes,
    and `merge_artifact_refs` (`contracts/state.py`) raises on a same-key,
    different-digest collision when LangGraph merges state.
    """

    envelope = _base_envelope(state, artifact_type, parents=parents)
    envelope["artifact_id"] = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, artifact_type
    )
    return envelope


def _require_stage_ref(state: OptimizationState, node_id: str, artifact_type: str) -> ArtifactRef:
    artifact_id = _stage_artifact_id(_required_state_str(state, "case_id"), node_id, artifact_type)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required {artifact_type} produced by {node_id}")


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    raise ValueError(f"missing required artifact ref: {artifact_type}")


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"A3 state is missing required field {key!r}")
    return value


__all__ = ["build_a3_registrations", "build_a3_runtime"]
