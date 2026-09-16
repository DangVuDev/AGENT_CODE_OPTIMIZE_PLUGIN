"""CLI: run the real pipeline through A1 -> A2 -> A3 -> C0 -> S01 (Rank &
Select) -> S02 (Plan & Task List), then STOP.

Prints the real sealed `SelectedSolution`, `ExecutionPlan` and `TaskList` --
S03 (Implement) is never invoked, so this script never edits the target
repository's real source files. It is `scripts/optimize_and_apply.py` cut
short on purpose, for exactly this "show me the plan, don't touch my repo
yet" use case.

Reuses `scripts/optimize.py`'s own CLI surface verbatim (including
--execution-profile docker_compose and every --eval-* flag), since S01/S02
consume the exact same `ConvergedCase` `optimize.py` already knows how to
build -- see its own --help for the full flag reference.

    python scripts/plan_to_tasklist.py <path-to-repo> --feature-id ... \
        --metric ... --direction ... --target ... --unit ... --command-id ...

    python scripts/plan_to_tasklist.py fixtures/external/realworld-go \
        --feature-id user-domain --metric suite_runtime_ms --direction minimize \
        --target 5000 --unit ms --guardrail-metric-id correctness \
        --execution-profile docker_compose --compose-file optimizer.compose.yaml \
        --eval-service testbed --eval-command /workspace/scripts/optimizer-evaluate-users.sh \
        --eval-metrics suite_runtime_ms,correctness --application-services testbed \
        --maximum-worker-seconds 600

Any S01/S02 human-in-the-loop halt (S01.80 ranking approval, S02.90 plan
approval) is auto-resumed as this CLI's own owner -- the same stance every
script in this family takes (see `optimize_and_apply.py`'s
`_auto_resume_until_terminal` docstring). Requires a real `ModelProviderPort`
(ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY/DEEPSEEK_API_KEY, or a
reachable local Ollama server): S02.30 drafts a real plan from A3's real
strategy text, and `LocalScriptedModelProvider` is deliberately not extended
to fake that -- this script stops honestly at C0 without one, exactly like
`optimize_and_apply.py` does.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any, cast

from _lane1_common import (
    LocalScriptedModelProvider,
    build_control_plane,
    model_selection_kwargs_from_args,
    read_model,
    ref_by_type,
    select_model_provider,
)
from optimize import build_payload, parse_args, seed_payload

from production_optimizer.adapters.production import report_model_provider_error
from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import (
    NodePorts,
    NodeRuntime,
    build_a1_runtime,
    build_a2_runtime,
    build_a3_runtime,
    build_c0_runtime,
    build_s01_registrations,
    build_s02_registrations,
)
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.resume import resume_case
from production_optimizer.contracts.a3 import (
    CitationResolutionReportSet,
    FindingDraftSet,
    FindingJudgementSet,
    ProblemSignalSet,
)
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.contracts.s01 import SelectedSolution
from production_optimizer.contracts.s02 import ExecutionPlan, PlanQualityReport, TaskList
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_c0_graph,
    build_s01_graph,
    build_s02_graph,
)

_TENANT_ID = "TENANT-CLI"
CONTROL_PLANE = build_control_plane(service_name="production-optimizer-plan")


def print_control_plane() -> None:
    state = "durable" if CONTROL_PLANE.durable else "NOT durable"
    print(f"[control-plane] {state}")
    for note in CONTROL_PLANE.notes:
        print(f"[control-plane]   {note}")


def _auto_resume(
    graph: Any, state: dict[str, Any], *, actor_id: str, thread_id: str, case_id: str
) -> dict[str, Any]:
    """Mirrors `optimize_and_apply.py`'s `_auto_resume_until_terminal`, scoped
    to whichever single subgraph (S01 or S02) is passed in -- each subgraph
    ends at its own `END`, so this only ever resumes halts belonging to it."""

    attempt = 0
    while state.get("pending_interrupt") is not None:
        attempt += 1
        interrupt = state["pending_interrupt"]
        decision = "approve" if "approve" in interrupt.allowed_decisions else (
            interrupt.allowed_decisions[0]
        )
        print(
            f"  [auto-resume #{attempt}] {interrupt.stage} halted for "
            f"{interrupt.allowed_decisions} -- auto-deciding {decision!r} as this CLI's owner"
        )
        now = datetime.now(UTC)
        command = ResumeInterruptCommand(
            command_id=f"{case_id}-resume-{attempt}", tenant_id=_TENANT_ID, case_id=case_id,
            thread_id=thread_id, interrupt_id=interrupt.interrupt_id, actor_id=actor_id,
            actor_roles={interrupt.required_actor_role}, decision=decision,
            artifact_digest=interrupt.artifact_digest, policy_version=interrupt.policy_version,
            issued_at=now,
        )
        actor = ActorContext(
            actor_id=actor_id, tenant_id=_TENANT_ID, roles={interrupt.required_actor_role},
            authenticated_at=now,
        )
        state = resume_case(
            graph=graph, state=cast("OptimizationState", state), command=command, actor=actor,
            now=now,
        )
    return state


def _print_a3_diagnostics(store: Any, state: dict[str, Any]) -> None:
    """A3.40's finding generation is a real LLM call, so its output -- and
    therefore whether A3.51 finds anything solid enough to keep -- is
    genuinely non-deterministic run to run. When A3 rejects (no sealed
    SolutionPortfolio), print the real chain of *why* instead of a bare
    routes dict: signals -> drafts -> citation resolution -> judgement."""

    print()
    signal_ref = ref_by_type(state, "ProblemSignalSet")
    if signal_ref is not None:
        signals = read_model(store, _TENANT_ID, signal_ref, ProblemSignalSet)
        print(f"ProblemSignalSet: {len(signals.signals)} signal(s)")
        for signal in signals.signals:
            print(f"  - {signal.signal_id}: {signal.description}")

    draft_ref = ref_by_type(state, "FindingDraftSet")
    if draft_ref is not None:
        draft_set = read_model(store, _TENANT_ID, draft_ref, FindingDraftSet)
        print(f"\nFindingDraftSet: {len(draft_set.drafts)} draft(s)")
        for draft in draft_set.drafts:
            print(f"  - {draft.finding_id} ({draft.claim_type}): {draft.causal_claim}")
            print(f"    supporting_evidence_ids: {draft.supporting_evidence_ids}")
        if draft_set.generation_failures:
            print(f"  generation_failures: {draft_set.generation_failures}")

    citation_ref = ref_by_type(state, "CitationResolutionReportSet")
    if citation_ref is not None:
        citations = read_model(store, _TENANT_ID, citation_ref, CitationResolutionReportSet)
        print(f"\nCitationResolutionReportSet: {len(citations.reports)} report(s)")
        for report in citations.reports:
            print(f"  - {report.finding_id}: all_resolved={report.all_resolved}")
            for entry in report.entries:
                print(
                    f"      {entry.evidence_id}: resolved={entry.resolved} "
                    f"in_scope={entry.in_scope} supports={entry.supports_statement} "
                    f"reason={entry.reason}"
                )

    judgement_ref = ref_by_type(state, "FindingJudgementSet")
    if judgement_ref is not None:
        judgements = read_model(store, _TENANT_ID, judgement_ref, FindingJudgementSet)
        print(f"\nFindingJudgementSet: {len(judgements.judgements)} judgement(s)")
        for judgement in judgements.judgements:
            print(f"  - {judgement.finding_id}: verdict={judgement.verdict}")
            print(f"    reasons: {judgement.reasons}")

    print(
        "\nA3.51 requires >=1 finding with all_resolved citations to seal a real "
        "Finding -- with none, FindingSet cannot be constructed (min_length=1), so "
        "A3 honestly rejects instead of fabricating a finding. This is real LLM "
        "non-determinism (A3.40's generation call), not a bug; rerunning may "
        "produce a finding whose cited evidence actually resolves."
    )


def main() -> None:
    args = parse_args(sys.argv[1:])
    repo_path = args.repo_path.resolve()
    if not repo_path.exists():
        raise SystemExit(f"repo path does not exist: {repo_path}")
    args.repo_path = repo_path
    print_control_plane()
    thread_id = f"THREAD-{args.case_id}"

    print(f"Target codebase: {repo_path}")
    print(f"Feature: {args.feature_id}")
    print(f"Criterion: {args.metric} {args.direction} {args.target}{args.unit}")
    print()

    store = CONTROL_PLANE.artifacts
    payload_ref = seed_payload(store, args.case_id, build_payload(args))

    print("=== A1: parsing intent into a sealed OptimizationRequest ===")
    a1_ports = NodePorts(
        artifacts=store, intents=CONTROL_PLANE.intents, telemetry=CONTROL_PLANE.telemetry,
        policy=CONTROL_PLANE.policy,
    )
    state = build_a1_graph(build_a1_runtime(ports=a1_ports)).invoke(
        {
            "case_id": args.case_id, "thread_id": thread_id, "tenant_id": _TENANT_ID,
            "entrypoint": "manual", "lane": "manual", "baseline_mode": "active_collection",
            "actor_context": ActorContext(
                actor_id=args.actor_id, tenant_id=_TENANT_ID, roles={"owner"},
                authenticated_at=datetime.now(UTC),
            ),
            "artifact_refs": [payload_ref],
        }
    )
    if state.get("pending_interrupt") is not None or state.get("request_ref") is None:
        print(f"A1 did not produce a request (routes: {state.get('node_routes')}).")
        return
    print(f"A1 completed: {sorted(state.get('completed_nodes', []))}")
    print()

    print("=== A2: collecting real evidence ===")
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
    )
    try:
        a2_ports = NodePorts(
            artifacts=store, intents=CONTROL_PLANE.intents, policy=CONTROL_PLANE.policy,
            telemetry=CONTROL_PLANE.telemetry, workers=broker,
        )
        state = build_a2_graph(build_a2_runtime(ports=a2_ports)).invoke(state)
    finally:
        broker.close()
    if ref_by_type(state, "BaselineSnapshot") is None:
        print(f"A2 did not reach a sealed BaselineSnapshot (routes: {state.get('node_routes')}).")
        return
    print("A2 completed.")
    print()

    print("=== A3: analyzing evidence and proposing strategies ===")
    model_provider, model_id = select_model_provider(
        tenant_id=_TENANT_ID,
        thread_id=thread_id,
        **model_selection_kwargs_from_args(args),
    )
    a3_ports = NodePorts(
        artifacts=store, intents=CONTROL_PLANE.intents, policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry, model=model_provider, model_id=model_id,
    )
    state = build_a3_graph(
        build_a3_runtime(ports=a3_ports), checkpointer=CONTROL_PLANE.checkpointer
    ).invoke(state, config={"configurable": {"thread_id": thread_id}})
    if ref_by_type(state, "SolutionPortfolio") is None:
        print(f"A3 did not reach a sealed SolutionPortfolio (routes: {state.get('node_routes')}).")
        _print_a3_diagnostics(store, state)
        return
    print("A3 completed.")
    print()

    print("=== C0: converging the case ===")
    c0_ports = NodePorts(
        artifacts=store, intents=CONTROL_PLANE.intents, policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry,
    )
    state = build_c0_graph(build_c0_runtime(ports=c0_ports)).invoke(state)
    if ref_by_type(state, "ConvergedCase") is None:
        print(f"Case did not converge (routes: {state.get('node_routes')}).")
        return
    print("C0 converged.")
    print()

    if isinstance(model_provider, LocalScriptedModelProvider):
        print(
            "No real LLM available (no ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY/"
            "DEEPSEEK_API_KEY and no reachable local Ollama server): stopping here.\n"
            "S02.30 (plan drafting) needs a model that can really read and reason about "
            "this repository's code -- set a real API key (see .env.example) or run a "
            "local Ollama server to continue past C0."
        )
        return

    print("=== S01: Rank & Select ===")
    s01_ports = NodePorts(
        artifacts=store, intents=CONTROL_PLANE.intents, policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry, model=model_provider, model_id=model_id,
    )
    s01_graph = build_s01_graph(NodeRuntime(build_s01_registrations(), ports=s01_ports))
    state = s01_graph.invoke(state)
    state = _auto_resume(
        s01_graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
    )
    selected_ref = ref_by_type(state, "SelectedSolution")
    if selected_ref is None:
        print(f"S01 did not select a solution (routes: {state.get('node_routes')}).")
        return
    selected = read_model(store, _TENANT_ID, selected_ref, SelectedSolution)
    print(f"Selected strategy: {selected.strategy_id!r} (decision={selected.approval.decision})")
    if selected.excluded_strategy_ids:
        print(f"Excluded strategies: {selected.excluded_strategy_ids}")
    print()

    print("=== S02: Plan & Task List ===")
    s02_ports = NodePorts(
        artifacts=store, intents=CONTROL_PLANE.intents, policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry, model=model_provider, model_id=model_id,
    )
    s02_graph = build_s02_graph(
        NodeRuntime(build_s02_registrations(), ports=s02_ports),
        checkpointer=CONTROL_PLANE.checkpointer,
    )
    state = s02_graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
    state = _auto_resume(
        s02_graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
    )

    quality_ref = ref_by_type(state, "PlanQualityReport")
    if quality_ref is not None:
        quality = read_model(store, _TENANT_ID, quality_ref, PlanQualityReport)
        print(f"Plan quality gate: passed={quality.passed}")
        for result in quality.results:
            marker = "OK" if result.passed else "FAIL"
            print(f"  [{marker}] {result.dimension}: {result.detail or ''}")
        draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
        path_repairs = [str(item) for item in cast("list[Any]", draft.get("path_repairs") or [])]
        if path_repairs:
            print("  Path repairs:")
            for repair in path_repairs:
                print(f"    - {repair}")
        critique = cast("dict[str, Any]", state.get("s02_critique") or {})
        ignored_ungrounded = [
            str(item) for item in cast("list[Any]", critique.get("ignored_ungrounded") or [])
        ]
        if ignored_ungrounded:
            print("  Ignored ungrounded critic concerns:")
            for concern in ignored_ungrounded:
                print(f"    - {concern}")
        print()

    plan_ref = ref_by_type(state, "ExecutionPlan")
    tasks_ref = ref_by_type(state, "TaskList")
    if plan_ref is None or tasks_ref is None:
        print(
            "S02 did not produce a sealed plan/task list -- the quality gate "
            "(S02.81) never seals ExecutionPlan/TaskList unless every dimension "
            "passes, even after the one bounded redraft."
        )
        draft = cast("dict[str, Any]", state.get("s02_plan_draft") or {})
        critique = cast("dict[str, Any]", state.get("s02_critique") or {})
        draft_phases = cast("list[Any]", draft.get("phases") or [])
        draft_tasks = cast("list[Any]", draft.get("tasks") or [])
        if draft_phases or draft_tasks:
            print(
                f"\nLast REJECTED draft (never sealed, shown for visibility only -- "
                f"{len(draft_phases)} phase(s), {len(draft_tasks)} task(s)):"
            )
            for phase in draft_phases:
                print(
                    f"  Phase {phase.sequence} [{phase.phase_id}] "
                    f"({phase.phase_kind}, risk={phase.risk_tier})"
                )
                print(
                    f"    treatment: {phase.treatment.variable}: "
                    f"{phase.treatment.before!r} -> {phase.treatment.after!r}"
                )
                print(f"    done_criteria: {phase.done_criteria}")
                print(f"    affected_criteria: {phase.affected_criteria}")
                print(f"    validation_command_ids: {phase.validation_command_ids}")
            for task in draft_tasks:
                print(f"  [{task.status}] {task.task_id} (phase={task.phase_id})")
                print(f"    objective: {task.objective}")
                if task.files:
                    print(f"    files: {task.files}")
            concerns = [str(item) for item in cast("list[Any]", critique.get("concerns") or [])]
            if concerns:
                print(f"\nCritic concerns: {concerns}")
        return

    plan = read_model(store, _TENANT_ID, plan_ref, ExecutionPlan)
    tasks = read_model(store, _TENANT_ID, tasks_ref, TaskList)

    print(f"ExecutionPlan: {len(plan.phases)} phase(s)")
    for phase in plan.phases:
        print(
            f"  Phase {phase.sequence} [{phase.phase_id}] "
            f"({phase.phase_kind}, risk={phase.risk_tier})"
        )
        print(
            f"    treatment: {phase.treatment.variable}: "
            f"{phase.treatment.before!r} -> {phase.treatment.after!r}"
        )
        print(f"    done_criteria: {phase.done_criteria}")
        print(f"    affected_criteria: {phase.affected_criteria}")
        print(f"    validation_command_ids: {phase.validation_command_ids}")
        print(
            f"    rollback_trigger: {phase.rollback_trigger} "
            f"(deadline {phase.rollback_deadline_seconds}s)"
        )
    print()

    print(f"TaskList: {len(tasks.tasks)} task(s)")
    for task in tasks.tasks:
        print(f"  [{task.status}] {task.task_id} (phase={task.phase_id}, owner={task.owner})")
        print(f"    objective: {task.objective}")
        if task.files:
            print(f"    files: {task.files}")
        if task.symbols:
            print(f"    symbols: {task.symbols}")
        if task.depends_on:
            print(f"    depends_on: {task.depends_on}")
        if task.proposed_creation:
            print("    (proposed_creation: this file does not exist yet)")

    print()
    print(
        "(stopped here by design -- S03/Implement was never invoked, so the target "
        "repository's real source files were never touched)"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # See `optimize.py`/`optimize_and_apply.py`: `ports.model.complete()`
        # calls are bare on purpose, so `report_model_provider_error` is the
        # one place that turns a deferred/permanent/bare-retryable model
        # failure into a clean message + exit code; anything else re-raises.
        raise SystemExit(report_model_provider_error(error)) from None
