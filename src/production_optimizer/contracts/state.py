from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from .artifacts import ArtifactRef
from .events import EventRef
from .interrupts import InterruptEnvelope


def merge_artifact_refs(left: list[ArtifactRef], right: list[ArtifactRef]) -> list[ArtifactRef]:
    """Deterministically merge parallel references and reject digest conflicts."""

    merged: dict[tuple[str, str], ArtifactRef] = {}
    for ref in [*left, *right]:
        key = (ref.artifact_type, ref.artifact_id)
        existing = merged.get(key)
        if existing is not None and existing.content_digest != ref.content_digest:
            raise ValueError(f"artifact reference conflict for {key[0]}:{key[1]}")
        merged[key] = ref
    return [merged[key] for key in sorted(merged)]


def merge_node_ids(left: list[str], right: list[str]) -> list[str]:
    """Merge branch completion markers in stable catalogue order."""

    return sorted(set(left) | set(right), key=lambda value: (value.split(".")[0], float(value[1:])))


def merge_node_routes(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Merge per-node routes and reject nondeterministic replay outcomes."""

    merged = dict(left)
    for node_id, route in right.items():
        existing = merged.get(node_id)
        if existing is not None and existing != route:
            raise ValueError(f"node route conflict for {node_id}: {existing!r} != {route!r}")
        merged[node_id] = route
    return dict(sorted(merged.items()))


def latest_node(left: str, right: str) -> str:
    """Select a stable progress marker when parallel branches complete."""

    if "." not in left:
        return right
    if "." not in right:
        return left
    left_stage, left_number = left.split(".", maxsplit=1)
    right_stage, right_number = right.split(".", maxsplit=1)
    return max((left_stage, int(left_number), left), (right_stage, int(right_number), right))[2]


class OptimizationState(TypedDict, total=False):
    """Compact root state.

    Raw source, evidence, command output, model transcripts, and secrets are
    deliberately absent. Business-stage fields are references reserved for
    later Lane 1 work.
    """

    case_id: str
    thread_id: str
    tenant_id: str
    lane: Literal["manual", "automatic"]
    entrypoint: Literal["manual", "discovery", "qualified"]
    baseline_mode: Literal["active_collection", "historical_recovery"]
    # Trusted host-authenticated identity for manual intake. Kept as Any to
    # avoid coupling graph state serialization to the platform contract.
    actor_context: Any
    status: str
    current_node: Annotated[str, latest_node]
    request_ref: ArtifactRef
    baseline_ref: ArtifactRef
    solution_portfolio_ref: ArtifactRef
    convergence_ref: ArtifactRef
    pending_interrupt: InterruptEnvelope | None
    completed_nodes: Annotated[list[str], merge_node_ids]
    node_routes: Annotated[dict[str, str], merge_node_routes]
    artifact_refs: Annotated[list[ArtifactRef], merge_artifact_refs]
    error_refs: Annotated[list[ArtifactRef], merge_artifact_refs]
    event_refs: Annotated[list[EventRef], operator.add]
    # A3.81<->A3.82<->A3.60 revision loop bookkeeping (see
    # `orchestration/subgraphs/a3.py`'s conditional edges). Both use a
    # summing reducer because each pass through the loop contributes an
    # independent increment, never a full replacement.
    a3_revision_attempts: Annotated[int, operator.add]
    a3_model_tokens_spent: Annotated[int, operator.add]
    # Set by `application.resume.resume_case` right before re-invoking a
    # graph whose run previously ended on `pending_interrupt`. A handler that
    # can halt for approval (e.g. A1.90) reads this to honor an
    # out-of-band decision instead of re-deriving one, and `resume_attempts`
    # (summing reducer, mirrors `a3_revision_attempts`) makes the resumed
    # call's idempotency key distinct so `NodeRuntime` re-executes it rather
    # than replaying the cached pre-resume (halted) result.
    # Typed `Any`, not `contracts.commands.ResumeInterruptCommand`, solely to
    # avoid a real import cycle (`commands.py` constructs `OptimizationState`
    # itself); every actual value stored here is still a real
    # `ResumeInterruptCommand` instance -- see `application.resume.resume_case`.
    resume_command: Any | None
    resume_attempts: Annotated[int, operator.add]
    # Which node_id `resume_command`/`resume_attempts` apply to (the
    # `InterruptEnvelope.stage` that halted the run) -- scopes the
    # idempotency-key bump in `NodeRuntime._derive_idempotency_key` to that
    # one node so every other, already-completed node still cache-hits on
    # resume instead of recomputing (and picking up a fresh `created_at`,
    # which would then conflict with its earlier cached artifact digest).
    resume_target_node: str | None
    # A halting node's own proposal (plain dict, node-specific shape) that a
    # human is being asked to approve/reject -- e.g. A2.31's LLM-suggested
    # `RepositoryCommand` when neither CI config nor pyproject.toml convention
    # detection found one. Not a reducer-guarded channel (only ever written
    # by the one node that halted, so no cross-node conflict is possible):
    # `application.resume.resume_case` deliberately leaves it untouched so
    # the resumed node can read back exactly what was approved without
    # re-deriving or re-asking an LLM for it.
    pending_command_proposal: Any | None
    # B1.40-81 accumulate the pieces of one eventual `DetectionReport`
    # (contracts/b1.py) across a linear chain and two fan-outs
    # (`B1_QUERY_FAN_OUT`, `B1_DETECTOR_FAN_OUT`) without any one of them
    # sealing/re-sealing that artifact themselves -- `merge_artifact_refs`
    # rejects two different contents under the same (type, artifact_id), so
    # a shared envelope re-sealed node-by-node would hard-conflict the
    # moment its content actually changed. Plain `operator.add`-reduced
    # lists sidestep that: each node contributes its own slice (a fan-out
    # branch's slice concatenates with its siblings', a linear node's with
    # nothing yet there), and B1.81 is the one place that reads all eight
    # back to seal the real, complete `DetectionReport` exactly once.
    # Typed `Any`, not `list[b1.RunGroup]` etc., for the same reason
    # `resume_command` is `Any`: `contracts.b1` importing `state.py` (for
    # other reasons) would make the reverse import a cycle.
    b1_run_groups: Annotated[list[Any], operator.add]
    b1_signals: Annotated[list[Any], operator.add]
    b1_feature_bindings: Annotated[list[Any], operator.add]
    b1_source_bindings: Annotated[list[Any], operator.add]
    b1_ownership_bindings: Annotated[list[Any], operator.add]
    b1_scores: Annotated[list[Any], operator.add]
    b1_qualification_decisions: Annotated[list[Any], operator.add]
    b1_cooldown_decisions: Annotated[list[Any], operator.add]
    # B2 has no fan-out (unlike B1) -- every node that touches these writes
    # exactly once, sequentially, so a plain last-value channel (no
    # `Annotated` reducer, same as `resume_command`/`pending_command_proposal`
    # above) is enough; nothing ever contends for the same field in one
    # superstep. `contracts.b2` value objects (`AnalysisStrategy`,
    # `DiscoveryAssumptionReport`, ...) live here rather than as sealed
    # top-level artifacts because they are working inputs to B2.22's
    # embedded A3 run and B2.60's final `ProposalEnvelope` assembly, not
    # independently-referenced artifacts themselves.
    b2_analysis_strategy: Any | None
    b2_model_context_package: Any | None
    b2_discovery_assumption_report: Any | None
    b2_staleness_decision: Any | None
    b2_narrative: str | None
    b2_routing_decision: Any | None
    b2_approval: Any | None
    # C0.10-50 each compute one category of `ConvergenceDecision`'s (see
    # `contracts/c0.py`) required inputs; C0's linear chain (no fan-out, see
    # `orchestration/subgraphs/c0.py`) means -- like the b2_* fields above --
    # a plain last-value field is enough, no reducer needed. C0.60 reads all
    # four back to assemble and seal the one real `ConvergenceDecision`.
    # Typed `Any` (lists of `contracts.c0` leaf types) for the same import-
    # cycle reason as `resume_command`/`b1_*`/`b2_*` above.
    c0_schema_results: Any | None
    c0_digest_chain: Any | None
    c0_equivalence_verdicts: Any | None
    c0_freshness_checks: Any | None
    # S01 (Rank & Select) and S02 (Plan & Task List) -- the shared workflow
    # steps that consume a `ConvergedCase` (see
    # `docs/project-blueprint/shared-workflow/`). Neither stage fans out, so
    # -- like the b2_*/c0_* fields above -- plain last-value fields are
    # enough; `s01_excluded_strategy_ids` is the one exception a caller may
    # pre-seed before re-invoking S01 after a Step 06 REVERT (BR-01-005).
    # Typed `Any` for the same import-cycle reason as `b1_*`/`b2_*`/`c0_*`.
    s01_decision_context: Any | None
    s01_eligible_strategy_ids: Any | None
    s01_normalized_factors: Any | None
    s01_scores: Any | None
    s01_ranked_strategy_ids: Any | None
    s01_sensitivity_flags: Any | None
    s01_routing_decision: Any | None
    s01_approval: Any | None
    s01_excluded_strategy_ids: Any | None
    s02_resolution_ok: Any | None
    s02_dependency_map: Any | None
    s02_plan_draft: Any | None
    s02_critique: Any | None
    s02_approval: Any | None
    # S02.81's deterministic-validation gate may route back to S02.30 for a
    # bounded redraft -- mirrors A3's `a3_revision_attempts` exactly, a
    # summing reducer since each pass contributes an independent increment.
    s02_revision_attempts: Annotated[int, operator.add]
    # S03 (Implement) -- one phase at a time from S02's `ExecutionPlan`, no
    # fan-out, so plain last-value fields again. `s03_active_phase_id`
    # persists across phases (S03.90 advances it); the rest are working
    # state for the *current* phase, overwritten each time S03 runs.
    s03_active_phase_id: Any | None
    s03_authorization: Any | None
    s03_workspace: Any | None
    s03_executor: Any | None
    s03_context: Any | None
    s03_tool_calls: Any | None
    s03_scope_report: Any | None
    s03_sanitation_report: Any | None
    # Written by S03.90: this phase's tasks are implemented, ready for S04
    # to verify -- not yet "done". `s03_completed_task_ids` (S03.10's own
    # active-phase gate) is written by `s04_handlers._s04_90` instead, and
    # only once S04 actually passes -- see s03_handlers._s03_90's docstring
    # for why a failed verification's real retry of the same phase depends
    # on keeping these two separate.
    s03_implemented_task_ids: Any | None
    s03_completed_task_ids: Any | None
    # S04 (Verify) -- reuses S03's own workspace (see s03_handlers._s03_90's
    # docstring), so no fan-out here either; working state overwritten each
    # time S04 runs for the active phase.
    s04_manifest: Any | None
    s04_check_results: Any | None
    s04_failure_attributions: Any | None
    # S04.80's mandatory-check failure (BR-04-004) and S06.50's FIX_ONE_PART
    # decision both send the graph back to S03 to really re-implement the
    # same active phase (`docs/project-blueprint/shared-workflow/
    # 09-langgraph-operating-model.md` names both as one "Implementation
    # repair" loop control) -- a summing reducer, mirrors
    # `a3_revision_attempts`/`s02_revision_attempts` exactly. Written (+1) by
    # `s04_handlers._s04_90` on a real failing pass and by `s06_handlers.
    # _s06_70` on a real FIX_ONE_PART route: every node from S03.10 through
    # S04.90 revisits itself once per pass, and without this counter
    # changing between passes, `NodeRuntime._derive_idempotency_key` would
    # derive the exact same key each time and replay pass 1's cached (stale)
    # result forever instead of re-executing -- see `_s04_90`'s docstring
    # for the full accounting of what else a real second pass requires
    # (pass-scoped `PatchArtifact`/`ExecutionProvenance`/`VerificationReport`
    # artifact_ids, and a reset of `s04_check_results`/
    # `s04_failure_attributions` at the start of each pass so a fixed check
    # doesn't get corrupted by a stale failing result accumulated from the
    # pass before it).
    s03_revision_attempts: Annotated[int, operator.add]
    # S05 (Controlled Remeasurement) -- reuses S03's own workspace exactly
    # like S04 does (no new isolation boundary), no fan-out, so plain
    # last-value fields overwritten each time S05 runs for the active phase.
    # Typed `Any` for the same import-cycle reason as `b1_*`/`b2_*`/`c0_*`.
    s05_protocol: Any | None
    s05_isolation: Any | None
    s05_treatment_aggregates: Any | None
    s05_quality: Any | None
    # S06 (Policy Decision) -- deterministic, no fan-out; `s06_decision`'s
    # `outcome` (KEEP/FIX_ONE_PART/REVERT/ESCALATE) is what
    # `orchestration/shared_workflow.py`'s outer conditional edge reads to
    # route onward, mirroring `_verification_outcome`'s existing
    # state-data-driven (not `NodeExecution.route`-driven) pattern for S04.
    s06_target_evaluations: Any | None
    s06_intake: Any | None
    s06_guardrail_evaluations: Any | None
    s06_attribution: Any | None
    s06_decision: Any | None
    s06_rollback: Any | None
    # S06.90 (Apply Accepted Change to Real Repository) -- no-op unless
    # `s06_decision["outcome"] == "KEEP"`, mirrors `s06_rollback`'s own
    # no-op-unless-REVERT shape. `applied=False` is a legitimate, honestly
    # reported outcome (see `contracts.s06.RepositoryApplyResult`'s
    # docstring), not a case-ending failure.
    s06_apply: Any | None
    # S07 (Report) -- only reachable via a real KEEP (see `s07_handlers`'s
    # module docstring); deterministic, no fan-out, working state overwritten
    # once per case close.
    s07_digest_chain: Any | None
    s07_chronology: Any | None
    s07_outcomes: Any | None
    s07_evidence: Any | None
    s07_source_change: Any | None
    s07_cost: Any | None
    s07_narrative: Any | None
