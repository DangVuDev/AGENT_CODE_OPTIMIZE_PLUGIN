"""Regression coverage for `resume_case`'s checkpointed branch.

Isolates a pure LangGraph-reducer mechanics bug from any real business
handler: `resume_case` used to spread `{**state, ...}` into
`graph.update_state(config, ...)`, and for `operator.add`-reducer fields
(`s03_revision_attempts`, `resume_attempts`, ...) that re-feeds the
channel's own already-persisted value back into itself, silently doubling
it on every checkpointed resume even though no second real pass occurred.
See `application/resume.py`'s own docstring on the fix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from production_optimizer.adapters.production import create_memory_checkpointer
from production_optimizer.application.resume import resume_case
from production_optimizer.contracts.artifacts import ArtifactRef
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
    # pass through ADVANCE would. Before the fix this read 2 (1 real write
    # plus the resume re-feeding the same 1 back through the `operator.add`
    # reducer).
    assert resumed["s03_revision_attempts"] == 1
    assert resumed["resume_attempts"] == 1


def test_checkpointed_resume_attempts_counts_linearly_across_two_halts() -> None:
    """A second independent halt+resume cycle must land on 2, not 3.

    Before the fix, `resume_attempts` was doubly wrong: `resume_case` fed a
    precomputed total (`old + 1`) through the same `operator.add` reducer,
    netting `old + (old + 1) = 2*old + 1` -- `3` after a second resume
    instead of `2`.
    """

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
