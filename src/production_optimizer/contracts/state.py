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
