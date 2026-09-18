"""Production handlers for S07 (Audited Report) -- assembles the one
human- and machine-readable record explaining what a case attempted,
measured, repaired and accepted (see
docs/project-blueprint/shared-workflow/07-report.md).

Only reachable via a real KEEP: `orchestration/shared_workflow.py`'s
`_policy_outcome` predicate only routes `"keep"` onward past S06 today (S08
Rollout is deferred, contract-only, per project decision, so KEEP simply
ends the case for now) -- `fix_one_part` loops back to S03 and `revert`/
`escalate` route to S01/END without ever reaching S07. `OptimizationReport.
case_outcome` is therefore honestly typed `Literal["KEEP"]`, not the doc's
full outcome space.

BR-07-001 ("missing evidence and failed work remain visible") is honored by
walking every real pass from 0 through the final `s03_revision_attempts`
value, not just the final passing one: an earlier pass that failed
verification (BR-04-004) or was judged FIX_ONE_PART (BR-06) is real, sealed
history (`s06_handlers._s06_80`'s docstring) this report surfaces rather
than silently drops. Chronology is honestly reconstructed from
`completed_nodes`/`node_routes` (catalog order plus real pass numbers), not
a true wall-clock event log: `contracts/state.py`'s `event_refs` channel is
declared but never populated by any handler in this codebase -- a real,
acknowledged gap, not hidden here.

S07.70's narrative is real LLM output when `ports.model` is wired (the same
generator-role pattern as A3.40/S02.30), and an equally real, deterministic
template when it is not -- BR-07-005 ("report generation never changes the
workflow decision") holds either way, since the narrative is prose only,
never a structured field the report's own gate reads back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import TypeAdapter

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import ComparabilityReport
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole
from production_optimizer.contracts.s01 import SelectedSolution
from production_optimizer.contracts.s02 import ExecutionPlan, TaskList
from production_optimizer.contracts.s03 import ExecutionProvenance, PatchArtifact
from production_optimizer.contracts.s04 import VerificationReport
from production_optimizer.contracts.s05 import Measurement, StatisticalReport
from production_optimizer.contracts.s06 import Decision, RepositoryApplyResult
from production_optimizer.contracts.s07 import (
    ChronologyEntry,
    CostSummary,
    EvidenceSummary,
    OptimizationReport,
    OutcomeEntry,
    SourceChangeSummary,
)
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="s07-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_POLICY_VERSION = "s07-report-v1"
_DEFAULT_S07_MODEL_ID = "claude-sonnet-5"
_S07_PROMPT_VERSION = "s07-narrative-v2"
_NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"narrative": {"type": "string"}},
    "required": ["narrative"],
    "additionalProperties": False,
}

_S07_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "S07.10": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S07.90": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _S07_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-workflow-s07",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="s07-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/07-report.md",
        slo="S07 completes within p95 < 60s",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production S07 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"s07_{node_id.replace('.', '_')}"
    return execute


def build_s07_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import S07_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in S07_NODE_IDS
    }


def build_s07_runtime(*, ports: NodePorts) -> NodeRuntime:
    return NodeRuntime(build_s07_registrations(), ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "S07.10":
            return _s07_10(state, ports)
        case "S07.20":
            return _s07_20(state, ports)
        case "S07.30":
            return _s07_30(state, ports)
        case "S07.40":
            return _s07_40(state, ports)
        case "S07.50":
            return _s07_50(state, ports)
        case "S07.60":
            return _s07_60(state, ports)
        case "S07.70":
            return _s07_70(state, ports)
        case "S07.80":
            return _s07_80(state, ports)
        case "S07.90":
            return _s07_90(state, ports)
        case _:
            return NodeExecution()


def _active_phase_id(state: OptimizationState) -> str:
    value = state.get("s03_active_phase_id")
    if not isinstance(value, str) or not value:
        raise ValueError("S07 state is missing s03_active_phase_id (S03-S06 must run first)")
    return value


def _final_pass_number(state: OptimizationState) -> int:
    return state.get("s03_revision_attempts", 0) or 0


def _stage(node_id: str, phase_id: str, pass_number: int) -> str:
    return f"{node_id}-{phase_id}-pass{pass_number}"


class _PassRecord:
    __slots__ = ("decision", "pass_number", "verification")

    def __init__(
        self, pass_number: int, verification: VerificationReport | None, decision: Decision | None
    ) -> None:
        self.pass_number = pass_number
        self.verification = verification
        self.decision = decision


def _reconstruct_passes(
    state: OptimizationState, ports: NodePorts, phase_id: str
) -> list[_PassRecord]:
    """Walk every real pass from 0 through the final one -- BR-07-001: an
    earlier pass that failed verification, or passed but was judged
    FIX_ONE_PART, is real sealed history, not silently dropped."""

    records: list[_PassRecord] = []
    for pass_number in range(_final_pass_number(state) + 1):
        verification_ref = _try_stage_ref(
            state, _stage("S04.90", phase_id, pass_number), "VerificationReport"
        )
        verification = (
            _read_model(ports, state, verification_ref, VerificationReport)
            if verification_ref is not None
            else None
        )
        decision_ref = _try_stage_ref(state, _stage("S06.80", phase_id, pass_number), "Decision")
        decision = (
            _read_model(ports, state, decision_ref, Decision) if decision_ref is not None else None
        )
        records.append(_PassRecord(pass_number, verification, decision))
    return records


def _s07_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real digest-chain verification for the shared-workflow portion of the
    case (S01-S06) -- trusts C0's own already-passed convergence gate for
    the A1-A3 portion rather than re-deriving it a second time."""

    phase_id = _active_phase_id(state)
    final_pass = _final_pass_number(state)
    # `SelectedSolution` is pass-scoped in `s01_handlers` (BR-01-005); a
    # plain first-match `_require_ref` would risk returning a stale pass's
    # selection if this case ever went through a REVERT round-trip back to
    # S01 before reaching a real KEEP.
    s01_pass = len(cast("list[str]", state.get("s01_excluded_strategy_ids") or []))
    selected_ref = _require_stage_ref(state, f"S01.90-pass{s01_pass}", "SelectedSolution")
    plan_ref = _require_ref(state, "ExecutionPlan")
    task_list_ref = _require_ref(state, "TaskList")
    patch_stage = _stage("S03.80", phase_id, final_pass)
    patch_ref = _require_stage_ref(state, patch_stage, "PatchArtifact")
    provenance_ref = _require_stage_ref(state, patch_stage, "ExecutionProvenance")
    verification_ref = _require_stage_ref(
        state, _stage("S04.90", phase_id, final_pass), "VerificationReport"
    )
    measurement_stage = _stage("S05.90", phase_id, final_pass)
    measurement_ref = _require_stage_ref(state, measurement_stage, "Measurement")
    decision_ref = _require_stage_ref(state, _stage("S06.80", phase_id, final_pass), "Decision")

    plan = _read_model(ports, state, plan_ref, ExecutionPlan)
    task_list = _read_model(ports, state, task_list_ref, TaskList)
    patch = _read_model(ports, state, patch_ref, PatchArtifact)
    provenance = _read_model(ports, state, provenance_ref, ExecutionProvenance)
    verification = _read_model(ports, state, verification_ref, VerificationReport)
    measurement = _read_model(ports, state, measurement_ref, Measurement)
    decision = _read_model(ports, state, decision_ref, Decision)

    links = [
        plan.selected_solution_digest == selected_ref.content_digest,
        task_list.execution_plan_digest == plan_ref.content_digest,
        patch.execution_plan_digest == plan_ref.content_digest,
        patch.execution_provenance_digest == provenance_ref.content_digest,
        provenance.execution_plan_digest == plan_ref.content_digest,
        verification.patch_digest == patch_ref.content_digest,
        verification.execution_provenance_digest == provenance_ref.content_digest,
        measurement.patch_digest == patch_ref.content_digest,
        measurement.baseline_digest == _require_ref(state, "BaselineSnapshot").content_digest,
        decision.measurement_digest == measurement_ref.content_digest,
        decision.verification_digest == verification_ref.content_digest,
    ]
    intact = all(links)
    route = NodeRoute.CONTINUE if intact else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"s07_digest_chain": {"intact": intact}})


def _s07_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real chronology from `completed_nodes`/`node_routes` -- see module
    docstring for why this is catalog-ordered, not wall-clock."""

    del ports
    routes = state.get("node_routes") or {}
    final_pass = _final_pass_number(state)

    entries = [
        ChronologyEntry(node_id=node_id, route=route, pass_number=final_pass).model_dump(
            mode="json"
        )
        for node_id, route in sorted(routes.items())
    ]
    repair_summary = f"repaired {final_pass} time(s)" if final_pass else "no repair needed"
    entries.append(
        ChronologyEntry(
            node_id="__phase__", route=repair_summary, pass_number=final_pass
        ).model_dump(mode="json")
    )
    return NodeExecution(updates={"s07_chronology": entries})


def _s07_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Classify every criterion's final outcome and every earlier repair
    pass -- BR-07-001/BR-07-003: nothing that happened is silently omitted."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    phase_id = _active_phase_id(state)
    passes = _reconstruct_passes(state, ports, phase_id)
    final = passes[-1]
    assert final.decision is not None and final.decision.outcome == "KEEP"

    outcomes: list[dict[str, Any]] = []
    for record in passes[:-1]:
        if record.verification is not None and not record.verification.passed:
            detail = "verification failed a mandatory check; the phase was re-implemented"
        elif record.decision is not None:
            detail = record.decision.rationale
        else:
            detail = "pass ended without a sealed decision"
        outcomes.append(
            OutcomeEntry(
                category="repaired",
                subject=phase_id,
                detail=detail,
                pass_number=record.pass_number,
            ).model_dump(mode="json")
        )

    met_by_criterion = {t.criterion_id: t.met for t in final.decision.target_evaluations}
    for criterion in request.criteria:
        met = met_by_criterion.get(criterion.criterion_id, False)
        outcomes.append(
            OutcomeEntry(
                category="completed" if met else "missing",
                subject=criterion.criterion_id,
                detail=f"{criterion.criterion_id} {'met' if met else 'did not meet'} its target",
                pass_number=final.pass_number,
            ).model_dump(mode="json")
        )
    return NodeExecution(updates={"s07_outcomes": outcomes})


def _s07_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Summarize the real, sealed evidence for every criterion -- raw
    numbers only, resolved to S05's own `StatisticalReport`/
    `ComparabilityReport` (BR-07-002)."""

    phase_id = _active_phase_id(state)
    final_pass = _final_pass_number(state)
    statistical_ref = _require_stage_ref(
        state, _stage("S05.80", phase_id, final_pass), "StatisticalReport"
    )
    statistical = _read_model(ports, state, statistical_ref, StatisticalReport)
    comparability_ref = _require_stage_ref(
        state, _stage("S05.70", phase_id, final_pass), "ComparabilityReport"
    )
    comparability = _read_model(ports, state, comparability_ref, ComparabilityReport)
    decision_ref = _require_stage_ref(state, _stage("S06.80", phase_id, final_pass), "Decision")
    decision = _read_model(ports, state, decision_ref, Decision)
    met_by_criterion = {t.criterion_id: t.met for t in decision.target_evaluations}

    evidence = [
        EvidenceSummary(
            criterion_id=effect.criterion_id,
            metric_id=effect.metric_id,
            baseline_mean=effect.baseline_mean,
            treatment_mean=effect.treatment_mean,
            absolute_change=effect.absolute_change,
            relative_change=effect.relative_change,
            comparable=comparability.comparable,
            met=met_by_criterion.get(effect.criterion_id, False),
        ).model_dump(mode="json")
        for effect in statistical.effects
    ]
    return NodeExecution(updates={"s07_evidence": evidence})


def _s07_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Record the real source change -- base revision, changed files and
    the rollback command already declared in the plan (BR-07-004: since
    this milestone's S07 is only reachable via KEEP, no rollback was
    executed for the final pass)."""

    phase_id = _active_phase_id(state)
    final_pass = _final_pass_number(state)
    patch_stage = _stage("S03.80", phase_id, final_pass)
    patch_ref = _require_stage_ref(state, patch_stage, "PatchArtifact")
    patch = _read_model(ports, state, patch_ref, PatchArtifact)
    plan = cast("ExecutionPlan", _read_required(ports, state, "ExecutionPlan"))
    phase = next(p for p in plan.phases if p.phase_id == phase_id)

    apply_stage = _stage("S06.90", phase_id, final_pass)
    apply_ref = _require_stage_ref(state, apply_stage, "RepositoryApplyResult")
    apply_result = _read_model(ports, state, apply_ref, RepositoryApplyResult)

    summary = SourceChangeSummary(
        phase_id=phase_id,
        base_revision=patch.base_revision,
        changed_files=patch.changed_files,
        patch_digest=patch_ref.content_digest,
        rollback_command=phase.rollback_command,
        rollback_executed=False,
        applied_to_repository=apply_result.applied,
        applied_branch_name=apply_result.branch_name,
        applied_commit_sha=apply_result.commit_sha,
        applied_failure_reason=apply_result.failure_reason,
    )
    return NodeExecution(updates={"s07_source_change": summary.model_dump(mode="json")})


def _s07_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Aggregate real model token usage across every pass's
    `ExecutionProvenance` -- A3's own `a3_model_tokens_spent` state field is
    declared but never populated by any A3 handler in this codebase (a real,
    acknowledged gap, not fabricated here), so only S03's real tokens are
    summed."""

    phase_id = _active_phase_id(state)
    input_tokens = 0
    output_tokens = 0
    for pass_number in range(_final_pass_number(state) + 1):
        stage = _stage("S03.80", phase_id, pass_number)
        ref = _try_stage_ref(state, stage, "ExecutionProvenance")
        if ref is None:
            continue
        provenance = _read_model(ports, state, ref, ExecutionProvenance)
        input_tokens += provenance.input_tokens
        output_tokens += provenance.output_tokens

    cost = CostSummary(
        model_input_tokens=input_tokens,
        model_output_tokens=output_tokens,
        phase_repair_attempts=_final_pass_number(state),
    )
    return NodeExecution(updates={"s07_cost": cost.model_dump(mode="json")})


def _build_narrative_facts(state: OptimizationState) -> str:
    """The structured facts S07.70's narrative may draw on -- and the only
    ones it may state (BR-07-002: report numbers resolve to raw evidence).

    Deliberately spells out every measured criterion with its real baseline/
    treatment numbers and met/not-met verdict rather than a bare count: a
    narrative cannot honestly report what was measured, or keep unmet
    criteria visible as BR-07-001 requires, from a summary that has already
    discarded the numbers.
    """

    outcomes = cast("list[dict[str, Any]]", state.get("s07_outcomes") or [])
    evidence = cast("list[dict[str, Any]]", state.get("s07_evidence") or [])
    cost = cast("dict[str, Any]", state.get("s07_cost") or {})
    source_change = cast("dict[str, Any]", state.get("s07_source_change") or {})

    lines = ["Decision: KEEP."]

    if source_change:
        lines.append("")
        lines.append("Source change:")
        lines.append(f"- phase: {source_change.get('phase_id')}")
        lines.append(f"- base revision: {source_change.get('base_revision')}")
        lines.append(f"- changed files: {source_change.get('changed_files')}")
        applied = source_change.get("applied_to_repository")
        lines.append(
            f"- applied to the real repository: {applied}"
            + (
                f" (reason: {source_change.get('applied_failure_reason')})"
                if not applied and source_change.get("applied_failure_reason")
                else ""
            )
        )

    lines.append("")
    if evidence:
        lines.append(f"Measured evidence ({len(evidence)} criteria):")
        for item in evidence:
            lines.append(
                f"- {item.get('criterion_id')} (metric {item.get('metric_id')}): "
                f"baseline={item.get('baseline_mean')} treatment={item.get('treatment_mean')} "
                f"absolute_change={item.get('absolute_change')} "
                f"relative_change={item.get('relative_change')} "
                f"comparable={item.get('comparable')} met={item.get('met')}"
            )
    else:
        lines.append("Measured evidence: none available for this case.")

    if outcomes:
        lines.append("")
        lines.append("Outcome ledger:")
        lines.extend(
            f"- {item.get('subject')}: {item.get('category')} ({item.get('detail')})"
            for item in outcomes
        )

    lines.append("")
    lines.append(
        f"Cost: {cost.get('model_input_tokens', 0)} input tokens, "
        f"{cost.get('model_output_tokens', 0)} output tokens, "
        f"{cost.get('phase_repair_attempts', 0)} phase repair attempt(s)."
    )
    return "\n".join(lines)


def _s07_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real LLM narrative when a model is wired (generator role, mirrors
    A3.40/S02.30); an equally real, deterministic template otherwise --
    BR-07-005 holds either way since nothing here feeds back into the
    already-sealed `s06_decision`."""

    facts = _build_narrative_facts(state)

    if ports.model is None:
        narrative = f"Case kept. {facts}"
        return NodeExecution(updates={"s07_narrative": narrative})

    request = ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id=ports.model_id or _DEFAULT_S07_MODEL_ID,
        prompt_version=_S07_PROMPT_VERSION,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You write the engineering summary of a completed code-optimization "
                    "case, using ONLY the structured facts supplied below.\n"
                    "\n"
                    "Rules this report is audited against:\n"
                    "- Never invent a number, file, metric, outcome or cause that is not "
                    "in the facts. Every number you state must appear verbatim in them "
                    "(BR-07-002).\n"
                    "- Failed, reverted, simplified and unfinished work stays visible. Do "
                    "not omit a criterion that was not met, and do not soften it -- state "
                    "plainly which criteria were met and which were not (BR-07-001).\n"
                    "- If the facts mark the measurement as not comparable, say so; a "
                    "measured improvement that is not comparable is not evidence of an "
                    "improvement.\n"
                    "- Do not recommend, decide or speculate about next steps. The "
                    "decision was already made and this summary never changes it "
                    "(BR-07-005).\n"
                    "\n"
                    "Write 3-6 sentences for an engineer who did not follow the case: what "
                    "was changed, what the measurements showed (with the real numbers), "
                    "which criteria were and were not met, and what it cost.\n"
                    'Respond with JSON: {"narrative": "..."}.'
                ),
            ),
            ModelMessage(role="user", content=facts),
        ],
        response_schema=_NARRATIVE_SCHEMA,
        max_output_tokens=400,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:S07.70:{_S07_PROMPT_VERSION}"
        ),
    )
    result = ports.model.complete(request)
    narrative = (
        cast("str", result.parsed_json.get("narrative", "")).strip()
        if result.valid_json and result.parsed_json
        else ""
    )
    return NodeExecution(updates={"s07_narrative": narrative or f"Case kept. {facts}"})


def _s07_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The only node that seals `OptimizationReport` -- publishes the real
    JSON and Markdown documents as blobs first (BR-07's "Store JSON,
    Markdown" outputs) and references both by digest."""

    phase_id = _active_phase_id(state)
    decision_ref = _require_stage_ref(
        state, _stage("S06.80", phase_id, _final_pass_number(state)), "Decision"
    )
    chronology = [
        ChronologyEntry.model_validate(raw)
        for raw in cast("list[dict[str, Any]]", state.get("s07_chronology") or [])
    ]
    outcomes = [
        OutcomeEntry.model_validate(raw)
        for raw in cast("list[dict[str, Any]]", state.get("s07_outcomes") or [])
    ]
    evidence = [
        EvidenceSummary.model_validate(raw)
        for raw in cast("list[dict[str, Any]]", state.get("s07_evidence") or [])
    ]
    source_change = SourceChangeSummary.model_validate(
        cast("dict[str, Any]", state.get("s07_source_change") or {})
    )
    cost = CostSummary.model_validate(cast("dict[str, Any]", state.get("s07_cost") or {}))
    narrative = cast("str", state.get("s07_narrative") or "")

    json_payload = canonical_json(
        {
            "case_id": _required_state_str(state, "case_id"),
            "phase_id": phase_id,
            "outcome": "KEEP",
            "chronology": [c.model_dump(mode="json") for c in chronology],
            "outcomes": [o.model_dump(mode="json") for o in outcomes],
            "evidence": [e.model_dump(mode="json") for e in evidence],
            "source_change": source_change.model_dump(mode="json"),
            "cost": cost.model_dump(mode="json"),
            "narrative": narrative,
        }
    )
    json_ref = ports.artifacts.put_blob(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=json_payload,
        content_digest=sha256_digest(json_payload),
        media_type="application/json",
    )

    markdown_lines = [
        f"# Optimization Report -- {_required_state_str(state, 'case_id')}",
        "",
        "**Outcome:** KEEP",
        "",
        "## Narrative",
        narrative,
        "",
        "## Evidence",
        *[
            f"- {e.criterion_id} ({e.metric_id}): {e.baseline_mean} -> {e.treatment_mean} "
            f"({'met' if e.met else 'not met'})"
            for e in evidence
        ],
        "",
        "## Source Change",
        f"- base revision: {source_change.base_revision}",
        f"- changed files: {', '.join(source_change.changed_files)}",
        f"- rollback command: {source_change.rollback_command or '(none declared)'}",
        (
            f"- applied to repository: True (branch {source_change.applied_branch_name}, "
            f"commit {source_change.applied_commit_sha})"
            if source_change.applied_to_repository
            else (
                f"- applied to repository: False "
                f"({source_change.applied_failure_reason or 'no reason recorded'})"
            )
        ),
        "",
        "## Cost",
        f"- model tokens: {cost.model_input_tokens} in / {cost.model_output_tokens} out",
        f"- phase repair attempts: {cost.phase_repair_attempts}",
    ]
    markdown_payload = "\n".join(markdown_lines).encode("utf-8")
    markdown_ref = ports.artifacts.put_blob(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=markdown_payload,
        content_digest=sha256_digest(markdown_payload),
        media_type="text/markdown",
    )

    report = _seal(
        OptimizationReport(
            **_stage_envelope(state, _stage("S07.80", phase_id, 0), "OptimizationReport"),
            case_outcome="KEEP",
            phase_id=phase_id,
            decision_digest=decision_ref.content_digest,
            chronology=chronology,
            outcomes=outcomes,
            evidence=evidence,
            source_change=source_change,
            cost=cost,
            narrative=narrative,
            json_report_digest=json_ref.content_digest,
            markdown_report_digest=markdown_ref.content_digest,
            policy_version=_POLICY_VERSION,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="S07.80")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _s07_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real completeness gate (BR-07-001): every A1 criterion and every real
    repair pass must have a matching outcome entry -- reads the just-sealed
    `OptimizationReport` back rather than trusting working state."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    phase_id = _active_phase_id(state)
    report_ref = _require_stage_ref(state, _stage("S07.80", phase_id, 0), "OptimizationReport")
    report = _read_model(ports, state, report_ref, OptimizationReport)

    subjects = {o.subject for o in report.outcomes}
    required_criteria = {c.criterion_id for c in request.criteria}
    required_passes = set(range(_final_pass_number(state)))  # every pass before the final one
    covered_passes = {o.pass_number for o in report.outcomes if o.category == "repaired"}

    complete = required_criteria <= subjects and required_passes <= covered_passes
    route = NodeRoute.CONTINUE if complete else NodeRoute.REJECTED
    return NodeExecution(route=route)


# ---------------------------------------------------------------------------
# Shared helpers (mirrors s05_handlers.py/s06_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "OptimizationRequest": cast("type[ArtifactEnvelope]", OptimizationRequest),
    "SelectedSolution": cast("type[ArtifactEnvelope]", SelectedSolution),
    "ExecutionPlan": cast("type[ArtifactEnvelope]", ExecutionPlan),
    "TaskList": cast("type[ArtifactEnvelope]", TaskList),
}


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> Any:
    ref = _require_ref(state, artifact_type)
    return _read_model(ports, state, ref, _MODEL_BY_TYPE[artifact_type])


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


def _read_model[T](
    ports: NodePorts, state: OptimizationState, ref: ArtifactRef, model: type[T]
) -> T:
    content = ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=ref)
    raw = TypeAdapter(dict[str, Any]).validate_json(content)
    if issubclass(cast("type[Any]", model), ArtifactEnvelope):
        raw.setdefault("content_digest", ref.content_digest)
    return cast("Any", model).model_validate(raw)


def _seal[T: ArtifactEnvelope](model: T) -> T:
    return model.model_copy(update={"content_digest": model_content_digest(model)})


def _base_envelope(
    state: OptimizationState, artifact_type: str, *, parents: list[str] | None = None
) -> dict[str, Any]:
    return {
        "artifact_id": f"{_required_state_str(state, 'case_id')}-{artifact_type}",
        "tenant_id": _required_state_str(state, "tenant_id"),
        "case_id": _required_state_str(state, "case_id"),
        "created_at": _now(),
        "producer": _PRODUCER,
        "policy_versions": {"s07": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _stage_artifact_id(case_id: str, node_id: str, artifact_type: str) -> str:
    return f"{case_id}-{node_id}-{artifact_type}"


def _stage_envelope(state: OptimizationState, node_id: str, artifact_type: str) -> dict[str, Any]:
    envelope = _base_envelope(state, artifact_type)
    envelope["artifact_id"] = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, artifact_type
    )
    return envelope


def _require_stage_ref(state: OptimizationState, node_id: str, artifact_type: str) -> ArtifactRef:
    ref = _try_stage_ref(state, node_id, artifact_type)
    if ref is None:
        raise ValueError(f"missing required {artifact_type} produced by {node_id}")
    return ref


def _try_stage_ref(
    state: OptimizationState, node_id: str, artifact_type: str
) -> ArtifactRef | None:
    artifact_id = _stage_artifact_id(_required_state_str(state, "case_id"), node_id, artifact_type)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type and ref.artifact_id == artifact_id:
            return ref
    return None


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    ref = _try_ref(state, artifact_type)
    if ref is None:
        raise ValueError(f"missing required artifact ref: {artifact_type}")
    return ref


def _try_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef | None:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    return None


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"S07 state is missing required field {key!r}")
    return value


__all__ = ["build_s07_registrations", "build_s07_runtime"]
