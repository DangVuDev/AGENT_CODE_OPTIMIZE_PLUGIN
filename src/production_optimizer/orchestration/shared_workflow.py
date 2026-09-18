# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

"""Composes the shared-workflow steps that consume a `ConvergedCase` (C0.70's
handoff) -- see `docs/project-blueprint/shared-workflow/`. Deliberately a
graph separate from `lanes.py`'s `build_lane_a_graph`/`build_lane_b_*_graph`:
a caller runs a lane graph to `ConvergedCase` first, then invokes this graph
with that state, mirroring exactly how B1's and B2's graphs are already two
separate `.invoke()` calls rather than one continuous graph.

S01->S02->PhaseLoop(S03<->S04<->S05<->S06)->S07 (Milestones 1-4 of the
shared-workflow plan) are wired, matching
`docs/project-blueprint/shared-workflow/09-langgraph-operating-model.md`'s
PhaseLoop shape:

    03 -> 04 -> 05 -> 06
     ^    |fail          |FIX_ONE_PART
     +----+              |
     ^                   |
     +-------------------+
    06 KEEP -> 07 -> (08 Rollout -- deferred, contract-only; case ends here)
    06 REVERT -> 01 (after a real, verified rollback)

Two real routing predicates read STATE DATA rather than a subgraph's own
`NodeExecution.route` (the same pattern, for the same reason): S04's
`s04_manifest["gate_passed"]` (set by `s04_handlers._s04_80`) and S06's
`s06_decision["outcome"]` (set by `s06_handlers._s06_50`/`_s06_70`) both
carry more than a plain `NodeRoute` value has members for (a mandatory-check
failure that loops back to S03 is not the same signal as any single node's
own route, and "loop back to S01 after a verified revert" has no `NodeRoute`
counterpart at all -- see `s06_handlers`'s module docstring for why that
was a deliberate choice, not a gap). `_policy_outcome` additionally checks
S06.90's own recorded *route* (via `node_runtime.route_for`, not mere
membership in `completed_nodes`) before consulting `s06_decision["outcome"]`:
S06.90 may itself halt on a real KEEP pending a real, resumable approval to
apply the accepted change to the real repository (`s06_handlers._s06_90`),
and `completed_nodes` alone cannot distinguish "halted" from "reached a real
terminal decision" since a node is recorded as completed the moment it runs
at all, regardless of which route it returned.

Both predicates also enforce `_MAX_PHASE_REPAIR_ATTEMPTS` against
`s03_revision_attempts` -- the one counter both a real S04.80 mandatory-check
failure and a real S06.50 FIX_ONE_PART decision bump (see `contracts/
state.py`'s docstring on that field). Neither `s04_handlers` nor
`s06_handlers` enforces a cap itself (mirrors A3's/S02's own bounded-revision
constants living beside the loop-driving logic, not inside the node that
merely contributes to the counter); `09-langgraph-operating-model.md`'s own
"Cross-Step Acceptance" section requires "all loops are bounded", so an
exhausted phase halts the case here rather than retrying forever.

This budget is shared across both failure classes by deliberate choice, not
an oversight: `s03_revision_attempts` also drives every S03/S04/S05/S06
artifact's pass-scoped id and `NodeRuntime`'s idempotency-key derivation
(see that field's own docstring in `contracts/state.py`), so splitting it
into independent per-class counters would require a second, derived "pass
number" for artifact scoping anyway -- reintroducing the same shared-state
coupling a split would be meant to avoid, while adding real risk to
idempotency-key/artifact-id uniqueness across passes (every S03.80/S04.90/
S05.*/S06.* artifact currently reads the *same* single value, guaranteed
consistent by construction). The real, acknowledged tradeoff: exhausting
the shared budget via one failure class (e.g. several genuine S04.80
verification failures) can leave less headroom than ideal for a fresh,
never-before-tried attempt of the other class (a S06.50 FIX_ONE_PART for an
unrelated, localized issue) -- a fairness/availability concern, not a
correctness one, since the case still halts safely rather than looping
forever. A full per-failure-class split (with its own separate pass-number
derivation for artifact scoping) is a larger, separate future refactor if
this fairness gap ever proves material in practice -- not attempted here.
"""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application import NodeRuntime
from production_optimizer.application.node_runtime import route_for
from production_optimizer.contracts.state import OptimizationState

from .subgraphs import (
    build_s01_graph,
    build_s02_graph,
    build_s03_graph,
    build_s04_graph,
    build_s05_graph,
    build_s06_graph,
    build_s07_graph,
)
from .subgraphs.common import END, START, StateGraph, completed

# Deliberately larger than a single-failure-class budget would need, since
# this one counter is shared between S04.80 verification failures and
# S06.50 FIX_ONE_PART decisions (see the module docstring above) -- enough
# headroom that a few genuine verification retries don't fully starve a
# later, unrelated fix attempt in the same phase.
_MAX_PHASE_REPAIR_ATTEMPTS = 5


def _verification_outcome(state: OptimizationState) -> str:
    if "S04.90" not in state.get("completed_nodes", []):
        return "halt"
    manifest = cast("dict[str, Any]", state.get("s04_manifest") or {})
    if manifest.get("gate_passed"):
        return "pass"
    if (state.get("s03_revision_attempts", 0) or 0) >= _MAX_PHASE_REPAIR_ATTEMPTS:
        return "halt"
    return "revision"


def _policy_outcome(state: OptimizationState) -> str:
    # Gated on S06.90's own *route*, not membership in `completed_nodes`:
    # `NodeRuntime._commit` adds a node to `completed_nodes` the moment it
    # runs at all, regardless of which route it returned, so "S06.90" is
    # already present the instant it first halts for approval -- checking
    # only membership would immediately fall through to `s06_decision`'s
    # already-sealed KEEP and route to "keep" while the real apply approval
    # is still pending, silently bypassing that gate. `route_for` reads
    # S06.90's most recently recorded route (the same helper `add_routed_edge`
    # itself uses), which is exactly "has S06.90 reached a *terminal*
    # decision (continue), or is it still waiting" -- also correctly
    # defaults to "continue" if S06.90 never ran at all (e.g. S06.10
    # rejected upstream), in which case `s06_decision` is empty and the
    # fallback below still returns "halt".
    if route_for("S06.90")(state) != "continue":
        return "halt"
    decision = cast("dict[str, Any]", state.get("s06_decision") or {})
    outcome = cast("str", decision.get("outcome", "")).lower()
    if (
        outcome == "fix_one_part"
        and (state.get("s03_revision_attempts", 0) or 0) >= _MAX_PHASE_REPAIR_ATTEMPTS
    ):
        return "halt"
    return outcome if outcome in {"keep", "fix_one_part", "revert", "escalate"} else "halt"


def build_shared_workflow_graph(
    runtime: NodeRuntime,
    *,
    checkpointer: Any | None = None,
    interrupt_after: list[str] | None = None,
) -> Any:
    """`interrupt_after` (e.g. `["S02"]`) lets a caller pause this one
    compiled graph right after a given top-level stage to inspect state --
    then resume with `graph.invoke(None, config=...)` -- without ever
    re-invoking S01/S02 as a second, separate graph on the same case.
    Re-running S01/S02 that way is unsafe: `s02_revision_attempts` (like
    `a3_revision_attempts`) increments on *every* pass through its owning
    node, including a clean first-try success (see `s02_handlers.
    _s02_81`'s own docstring on why), so it is never `0` again once that
    stage has completed once. A second, independent `.invoke()` of a stage's
    own standalone graph would derive every one of its nodes' idempotency
    keys with that now-nonzero suffix, missing the cache that stage's first
    run left keyed without it -- forcing a real recompute that reseals
    `ExecutionPlan` (not pass-scoped, unlike `PlanQualityReport`) with a
    fresh `created_at`/digest under the same `artifact_id`, hard-conflicting
    with the one already in `artifact_refs` via `merge_artifact_refs`. A
    single compiled graph with a real LangGraph interrupt has no such
    problem: every node still executes exactly once for the whole run,
    `interrupt_after`/`checkpointer` requires a checkpointer.
    """

    builder = StateGraph(OptimizationState)
    builder.add_node("S01", build_s01_graph(runtime))
    builder.add_node("S02", build_s02_graph(runtime))
    builder.add_node("S03", build_s03_graph(runtime))
    builder.add_node("S04", build_s04_graph(runtime))
    builder.add_node("S05", build_s05_graph(runtime))
    builder.add_node("S06", build_s06_graph(runtime))
    builder.add_node("S07", build_s07_graph(runtime))
    builder.add_edge(START, "S01")
    builder.add_conditional_edges("S01", completed("S01.90"), {"continue": "S02", "halt": END})
    builder.add_conditional_edges("S02", completed("S02.90"), {"continue": "S03", "halt": END})
    builder.add_conditional_edges("S03", completed("S03.90"), {"continue": "S04", "halt": END})
    builder.add_conditional_edges(
        "S04", _verification_outcome, {"pass": "S05", "revision": "S03", "halt": END}
    )
    builder.add_conditional_edges("S05", completed("S05.90"), {"continue": "S06", "halt": END})
    builder.add_conditional_edges(
        "S06",
        _policy_outcome,
        {
            "keep": "S07",
            "fix_one_part": "S03",
            "revert": "S01",
            "escalate": END,  # ROLLBACK_FAILED -- terminal, never silently reverted
            "halt": END,
        },
    )
    # S08 (Rollout) is deferred, contract-only (no CONNECTED_PRODUCTION
    # deployment target exists to route it against) -- the case simply ends
    # once S07's report is published, regardless of S07.90's own gate
    # outcome (both terminate the graph the same way today).
    builder.add_edge("S07", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)


__all__ = ["build_shared_workflow_graph"]
