# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnusedImport=false, reportUnusedFunction=false
# ruff: noqa: F401
from __future__ import annotations

import ast
import operator as operator_module
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ValidationCommandCandidate:
    command_id: str
    command_display: str
    source: Literal["compose_evaluation", "repository_command"]
    metric_ids: tuple[str, ...]


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
    "A3.51": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
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
    # Lazy import avoids a package-import cycle while keeping the registry
    # as the single ID-to-callable authority.
    from .registry import NODE_HANDLERS

    return NODE_HANDLERS[node_id]


def build_bound_a3_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import A3_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in A3_NODE_IDS
    }


# ---------------------------------------------------------------------------
# A3.10 / A3.11 — intake verification and evidence indexing
# ---------------------------------------------------------------------------


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
    bundle: EvidenceBundle,
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
        lines.append(
            "  Signal-supporting evidence IDs: " + ", ".join(signal.evidence_ids)
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
    lines.append("\nEvidence observations:")
    for item in bundle.evidence:
        support_hint = "supports_nonzero_claim=yes" if _evidence_supports_claim(item) else (
            "supports_nonzero_claim=no"
        )
        lines.append(
            f"- {item.evidence_id}: metric_id={item.metric_id} "
            f"evidence_type={item.evidence_type} value={item.value} unit={item.unit} "
            f"requirement_id={item.requirement_id} {support_hint}"
        )
    known_ids = sorted(entry.evidence_id for entry in catalog.entries)
    lines.append("\nAvailable evidence IDs (cite only these): " + ", ".join(known_ids))
    return "\n".join(lines)


def _evidence_supports_claim(item: Any) -> bool:
    value = getattr(item, "value", None)
    return isinstance(value, int | float) and not isinstance(value, bool) and value != 0


def _generate_finding_drafts(
    ports: NodePorts,
    state: OptimizationState,
    context: str,
    *,
    known_evidence_ids: set[str] | None = None,
    support_evidence_ids: set[str] | None = None,
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

    drafts, failures = _parse_finding_drafts(result.parsed_json)
    citation_failures = _invalid_finding_citations(
        drafts,
        known_evidence_ids=known_evidence_ids,
        support_evidence_ids=support_evidence_ids,
    )
    if citation_failures:
        allowed = ", ".join(sorted(known_evidence_ids or set()))
        supporting = ", ".join(sorted(support_evidence_ids or set()))
        repair = request.model_copy(
            update={
                "messages": [
                    *request.messages,
                    ModelMessage(
                        role="user",
                        content=(
                            "Your previous reply cited evidence IDs that are either outside the "
                            "allowed catalog or do not support the claim. Repair only the "
                            "citations and any claims that depended on them. "
                            f"Invalid citations: {'; '.join(citation_failures)}. "
                            f"Allowed evidence IDs: {allowed}. Evidence IDs that can support "
                            f"breach/violation claims: {supporting}. Neutral/pass evidence may "
                            "appear only in counterevidence_ids. Reply with only valid JSON "
                            "matching the schema."
                        ),
                    ),
                ],
                "idempotency_key": f"{request.idempotency_key}:repair-citations-1",
            }
        )
        repaired = ports.model.complete(repair)
        tokens += repaired.input_tokens + repaired.output_tokens
        if repaired.valid_json and repaired.parsed_json is not None:
            drafts, failures = _parse_finding_drafts(repaired.parsed_json)
            citation_failures = _invalid_finding_citations(
                drafts,
                known_evidence_ids=known_evidence_ids,
                support_evidence_ids=support_evidence_ids,
            )

    failures.extend(citation_failures)
    return drafts, failures, tokens


def _parse_finding_drafts(raw_json: dict[str, Any]) -> tuple[list[FindingDraft], list[str]]:
    drafts: list[FindingDraft] = []
    failures: list[str] = []
    for raw in raw_json.get("findings", []):
        try:
            drafts.append(FindingDraft.model_validate(raw))
        except ValidationError as exc:
            failures.append(f"{raw.get('finding_id', '<unknown>')}: {exc}")
    return drafts, failures


def _invalid_finding_citations(
    drafts: list[FindingDraft],
    *,
    known_evidence_ids: set[str] | None,
    support_evidence_ids: set[str] | None,
) -> list[str]:
    if known_evidence_ids is None:
        return []
    failures: list[str] = []
    for draft in drafts:
        unknown = [
            evidence_id
            for evidence_id in draft.supporting_evidence_ids
            if evidence_id not in known_evidence_ids
        ]
        if unknown:
            failures.append(
                f"{draft.finding_id}: unknown supporting_evidence_ids={sorted(unknown)}"
            )
        if support_evidence_ids is not None:
            unsupported = [
                evidence_id
                for evidence_id in draft.supporting_evidence_ids
                if evidence_id in known_evidence_ids and evidence_id not in support_evidence_ids
            ]
            if unsupported:
                failures.append(
                    f"{draft.finding_id}: unsupported supporting_evidence_ids="
                    f"{sorted(unsupported)}"
                )
    return failures


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


def _validation_command_candidates(
    request: OptimizationRequest, verification: VerificationManifest
) -> list[ValidationCommandCandidate]:
    candidates: list[ValidationCommandCandidate] = []
    if request.execution is not None:
        compose_file = request.execution.compose_file
        for evaluation in request.execution.evaluations:
            command = evaluation.command
            argv = " ".join(command.argv)
            working_dir = (
                f" --workdir {command.working_directory}" if command.working_directory else ""
            )
            candidates.append(
                ValidationCommandCandidate(
                    command_id=evaluation.evaluation_id,
                    command_display=(
                        f"docker compose -f {compose_file} exec{working_dir} "
                        f"{command.service} {argv}"
                    ),
                    source="compose_evaluation",
                    metric_ids=tuple(sorted(evaluation.expected_metric_ids)),
                )
            )

    request_metric_ids = {
        criterion.metric_id for criterion in request.criteria
    } | {guardrail.metric_id for guardrail in request.guardrails}
    for command in verification.commands:
        candidates.append(
            ValidationCommandCandidate(
                command_id=command.command_id,
                command_display=" ".join(command.argv),
                source="repository_command",
                metric_ids=tuple(sorted(request_metric_ids)),
            )
        )

    unique: dict[str, ValidationCommandCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.command_id, candidate)
    return list(unique.values())


def _expected_metric_movements(request: OptimizationRequest) -> dict[str, str]:
    movements: dict[str, str] = {}
    for criterion in request.criteria:
        if criterion.direction == "minimize":
            movement = f"decrease toward <= {criterion.target} {criterion.unit}"
        elif criterion.direction == "maximize":
            movement = f"increase toward >= {criterion.target} {criterion.unit}"
        else:
            movement = f"move toward == {criterion.target} {criterion.unit}"
        movements[criterion.metric_id] = movement

    for guardrail in request.guardrails:
        movements.setdefault(
            guardrail.metric_id,
            f"must remain {guardrail.operator} {guardrail.threshold} {guardrail.unit}",
        )
    return movements


def _validation_benchmark_protocol(candidates: list[ValidationCommandCandidate]) -> str:
    command_lines = [
        f"{candidate.command_id} [{candidate.source}]: {candidate.command_display}"
        for candidate in candidates
    ]
    return "rerun repository-owned validation commands via A2 worker protocol: " + "; ".join(
        command_lines
    )


def _build_strategy_context(
    findings: list[Finding],
    validation_commands: list[ValidationCommandCandidate] | None = None,
) -> str:
    lines = ["Findings eligible for strategy generation:"]
    for finding in findings:
        lines.append(
            f"- {finding.finding_id} ({finding.claim_type}, confidence={finding.confidence}): "
            f"{finding.causal_claim} [evidence={finding.supporting_evidence_ids}]"
        )
    lines.append("\nAvailable repository-owned validation commands:")
    if not validation_commands:
        lines.append("- none")
    else:
        for command in validation_commands:
            metrics = ", ".join(command.metric_ids) if command.metric_ids else "unknown"
            lines.append(
                f"- {command.command_id}: source={command.source}; "
                f"metrics={metrics}; command={command.command_display}"
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
        "phases. Each phase must contain exactly one logical treatment. If "
        "validation commands are listed, design strategies so they can be "
        "verified by those exact command IDs. Never invent validation commands."
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


_RISK_SENSITIVE_KEYWORDS = ("migrations/", "auth", "secrets", "security")


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
