"""Regression coverage for `resume_case`'s checkpointed branch, and for the
related subgraph-as-node reducer hazard in `orchestration/shared_workflow.py`.

Two independent, confirmed-real bugs shared one symptom class (a revision/
resume counter reading higher than the number of real passes that actually
occurred):

1. `resume_case` used to spread `{**state, ...}` into
   `graph.update_state(config, ...)`. When these counters were
   `Annotated[int, operator.add]` reducers, that re-fed each channel's own
   already-persisted value back into itself, doubling it on every
   checkpointed resume. Fixed by passing only the fields `resume_case`
   actually intends to change.
2. S03/S04/S05/S06 are each added to the outer `shared_workflow` graph as
   their own compiled subgraph NODE, and the outer graph genuinely
   revisits the same node (e.g. "S04") on a real revision pass. A compiled
   subgraph used as a node returns its own ABSOLUTE final channel value to
   the parent for every field in the shared schema, not a delta
   (confirmed via LangGraph's `pregel/_io.py` `map_output_values`). With a
   summing reducer, the parent's own `apply_writes` then added that
   already-absolute value on top of its own current value every time the
   node was revisited -- corrupting `s03_revision_attempts` after exactly
   one real failing pass, which in production caused `S04.10` to look up
   a `pass2` `PatchArtifact` that `S03.80` (seeing the correct value `1`)
   never sealed.

The actual fix for (2) is `contracts/state.py`'s `s03_revision_attempts`
(and `a3_revision_attempts`/`s02_revision_attempts`/`resume_attempts`)
no longer being `operator.add` reducers at all -- each writer computes and
assigns the new total itself, so last-write-wins is correct regardless of
how many times a subgraph-as-node round-trips its own absolute state
through the parent. See `application/resume.py`'s and `contracts/state.py`'s
own docstrings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from production_optimizer.adapters.production import create_memory_checkpointer
from production_optimizer.application.resume import resume_case
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.contracts.state import OptimizationState

_DIGEST = "sha256:" + "a" * 64
_TENANT = "TENANT-RESUME-TEST"
_CASE = "CASE-RESUME-TEST"
_THREAD = "THREAD-RESUME-TEST"


def _advance(state: OptimizationState) -> dict[str, Any]:
    """Simulates one real revision pass (e.g. S04.90's own real failure)."""

    return {"s03_revision_attempts": 1, "completed_nodes": ["S04.90"]}


def _halt(state: OptimizationState) -> dict[str, Any]:
    now = datetime.now(UTC)
    interrupt = InterruptEnvelope(
        interrupt_id=f"{state['case_id']}-HALT-{state.get('resume_attempts', 0)}",
        case_id=state["case_id"],
        thread_id=state["thread_id"],
        stage="HALT",
        artifact_digest=_DIGEST,
        allowed_decisions=["approve", "reject"],
        required_actor_role="owner",
        policy_version="bootstrap-v1",
        issued_at=now,
        expires_at=now.replace(year=now.year + 1),
    )
    return {"pending_interrupt": interrupt, "completed_nodes": ["S01.80"]}


def _continue(state: OptimizationState) -> dict[str, Any]:
    return {"completed_nodes": ["S01.90"]}


def _build_graph() -> Any:
    builder = StateGraph(OptimizationState)
    builder.add_node("ADVANCE", _advance)
    builder.add_node("HALT", _halt)
    builder.add_node("CONTINUE", _continue)
    builder.add_edge(START, "ADVANCE")
    builder.add_edge("ADVANCE", "HALT")
    builder.add_edge("HALT", "CONTINUE")
    builder.add_edge("CONTINUE", END)
    return builder.compile(checkpointer=create_memory_checkpointer())


def _resume_command(interrupt: InterruptEnvelope, *, command_id: str) -> ResumeInterruptCommand:
    return ResumeInterruptCommand(
        command_id=command_id,
        tenant_id=_TENANT,
        case_id=interrupt.case_id,
        thread_id=interrupt.thread_id,
        interrupt_id=interrupt.interrupt_id,
        actor_id="owner-1",
        actor_roles={"owner"},
        decision="approve",
        artifact_digest=interrupt.artifact_digest,
        policy_version=interrupt.policy_version,
        issued_at=datetime.now(UTC),
    )


def test_checkpointed_resume_does_not_double_count_reducer_fields() -> None:
    graph = _build_graph()
    config = {"configurable": {"thread_id": _THREAD}}

    seed: OptimizationState = {
        "case_id": _CASE,
        "thread_id": _THREAD,
        "tenant_id": _TENANT,
        "artifact_refs": [],
        "node_routes": {},
    }
    halted = graph.invoke(seed, config=config)

    assert halted["s03_revision_attempts"] == 1
    assert halted["pending_interrupt"] is not None

    interrupt = halted["pending_interrupt"]
    actor = ActorContext(
        actor_id="owner-1", tenant_id=_TENANT, roles={"owner"}, authenticated_at=datetime.now(UTC)
    )
    resumed = resume_case(
        graph=graph,
        state=halted,
        command=_resume_command(interrupt, command_id="resume-1"),
        actor=actor,
        now=datetime.now(UTC),
        config=config,
    )

    # The resume itself must not touch this counter -- only a REAL second
    # pass through ADVANCE would.
    assert resumed["s03_revision_attempts"] == 1
    assert resumed["resume_attempts"] == 1


def test_checkpointed_resume_attempts_counts_linearly_across_two_halts() -> None:
    """A second independent halt+resume cycle must land on 2, not 3."""

    builder = StateGraph(OptimizationState)
    builder.add_node("HALT", _halt)
    builder.add_node("CONTINUE", _continue)
    builder.add_edge(START, "HALT")
    builder.add_edge("HALT", "CONTINUE")
    builder.add_edge("CONTINUE", "HALT")
    graph = builder.compile(
        checkpointer=create_memory_checkpointer(), interrupt_after=["CONTINUE"]
    )
    config = {"configurable": {"thread_id": f"{_THREAD}-2"}}

    seed: OptimizationState = {
        "case_id": _CASE,
        "thread_id": f"{_THREAD}-2",
        "tenant_id": _TENANT,
        "artifact_refs": [],
        "node_routes": {},
    }
    state = graph.invoke(seed, config=config)
    actor = ActorContext(
        actor_id="owner-1", tenant_id=_TENANT, roles={"owner"}, authenticated_at=datetime.now(UTC)
    )

    # First halt + resume.
    interrupt = state["pending_interrupt"]
    state = resume_case(
        graph=graph,
        state=state,
        command=_resume_command(interrupt, command_id="resume-1"),
        actor=actor,
        now=datetime.now(UTC),
        config=config,
    )
    assert state["resume_attempts"] == 1

    # Second, independent halt + resume in the same thread.
    interrupt = state["pending_interrupt"]
    state = resume_case(
        graph=graph,
        state=state,
        command=_resume_command(interrupt, command_id="resume-2"),
        actor=actor,
        now=datetime.now(UTC),
        config=config,
    )
    assert state["resume_attempts"] == 2


def _inner_fail_once(state: OptimizationState) -> dict[str, Any]:
    """Mirrors `s04_handlers._s04_90`'s real writer: computes and assigns
    the new total itself (not a reducer delta)."""

    pass_number = state.get("s03_revision_attempts", 0) or 0
    return {"s03_revision_attempts": pass_number + 1, "completed_nodes": ["S04.90"]}


def _build_inner_subgraph() -> Any:
    """A minimal stand-in for `orchestration/subgraphs/s04.py`'s
    `build_s04_graph`: a compiled subgraph, with NO checkpointer of its
    own, that writes the shared `s03_revision_attempts` field."""

    builder = StateGraph(OptimizationState)
    builder.add_node("INNER", _inner_fail_once)
    builder.add_edge(START, "INNER")
    builder.add_edge("INNER", END)
    return builder.compile()


def test_subgraph_as_node_does_not_double_count_shared_reducer_field_on_revisit() -> None:
    """Reproduces the real production bug behind `orchestration/
    shared_workflow.py`'s S03/S04/S05/S06 PhaseLoop: a compiled subgraph
    used as a node (`builder.add_node("INNER", build_inner_subgraph())`)
    returns its OWN absolute final state for every shared-schema field, not
    a delta. If that field were still an `operator.add` reducer, the OUTER
    graph's own `apply_writes` would add that already-absolute value on top
    of its own current value every time the outer graph revisits "INNER" --
    silently doubling it on the second pass even though the inner node only
    ever wrote a genuine +1 once per visit. `s03_revision_attempts` being a
    plain last-value field (see `contracts/state.py`) is what prevents this;
    this test fails if that field regresses back to a summing reducer.
    """

    inner = _build_inner_subgraph()

    def _route(state: OptimizationState) -> str:
        return "again" if (state.get("s03_revision_attempts", 0) or 0) < 2 else "done"

    builder = StateGraph(OptimizationState)
    builder.add_node("OUTER", inner)
    builder.add_conditional_edges("OUTER", _route, {"again": "OUTER", "done": END})
    builder.add_edge(START, "OUTER")
    graph = builder.compile()

    state = graph.invoke(
        {
            "case_id": _CASE,
            "thread_id": f"{_THREAD}-3",
            "tenant_id": _TENANT,
            "artifact_refs": [],
            "node_routes": {},
        }
    )

    # The outer graph revisits "OUTER" exactly twice (pass 0 -> 1, then
    # 1 -> 2), and each visit's inner node contributes exactly one genuine
    # +1. Verified this assertion actually catches the regression: reverting
    # `s03_revision_attempts` back to `Annotated[int, operator.add]` makes
    # this read 4, not 2.
    assert state["s03_revision_attempts"] == 2
