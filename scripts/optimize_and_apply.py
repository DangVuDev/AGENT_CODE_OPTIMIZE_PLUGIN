"""CLI: run the *entire* real pipeline -- Lane 1 (A1 -> A2 -> A3 -> C0) plus
the shared workflow (S01 -> S02 -> PhaseLoop(S03<->S04<->S05<->S06) -> S07) --
against a real local repository, end to end.

    python scripts/optimize_and_apply.py <path-to-repo> --objective "Fix the failing tests"

This is `scripts/optimize.py` extended past `ConvergedCase`: once Lane 1
converges, this script feeds that exact state into
`orchestration.shared_workflow.build_shared_workflow_graph(...)` and keeps
driving it for real -- selecting a solution (S01), planning it (S02),
implementing one phase in a real isolated workspace (S03), verifying it with
the repository's own real commands (S04), remeasuring it (S05), deciding
KEEP/FIX_ONE_PART/REVERT (S06), and publishing a real audited report (S07).
Every real human-in-the-loop approval halt (S01.80/S02.90) is auto-resumed
as this CLI's own owner -- the same "this CLI always acts as the request's
own owner" stance `optimize.py` already takes at A1.90, just extended to the
shared workflow's own approval gates. `--case-id`/thread wiring reuses one
in-memory `MemoryArtifactStore`/`MemoryIntentLedger` for the whole run, same
as `optimize.py`.

S02.30 (plan drafting) and, depending on the selected strategy's risk tier,
S03.50 (code editing) need a `ModelProviderPort` that can actually read and
reason about this repository's real code -- `LocalScriptedModelProvider`
(`_lane1_common.py`) is deliberately *not* extended to fake that: its A3
stand-in already produces a real, evidence-grounded strategy shape, but a
non-literal, descriptive `Treatment` (see its own docstring), which no
honest deterministic executor can turn into a real code change. So this
script requires a real model (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/
`GEMINI_API_KEY`/`DEEPSEEK_API_KEY`, or a reachable local Ollama server) to
proceed past C0 -- without one, it prints the same real `ConvergedCase` info
`optimize.py` would and stops there, honestly, rather than fabricating a
plan or patch no model actually produced.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from _lane1_common import (
    LocalScriptedModelProvider,
    build_control_plane,
    read_model,
    ref_by_type,
    select_model_provider,
)

from production_optimizer.adapters.production import DeferredModelCallError, is_retryable
from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import (
    NodePorts,
    NodeRuntime,
    build_a1_runtime,
    build_a2_runtime,
    build_a3_runtime,
    build_c0_runtime,
    build_s01_registrations,
    build_s01_runtime,
    build_s02_registrations,
    build_s02_runtime,
    build_s03_registrations,
    build_s04_registrations,
    build_s05_registrations,
    build_s06_registrations,
    build_s07_registrations,
)
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.resume import resume_case
from production_optimizer.contracts.a1 import ManualCasePayload
from production_optimizer.contracts.a2 import BaselineSnapshot
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.c0 import ConvergedCase
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.contracts.s01 import SelectedSolution
from production_optimizer.contracts.s02 import ExecutionPlan, PlanQualityReport, TaskList
from production_optimizer.contracts.s06 import Decision
from production_optimizer.contracts.s07 import OptimizationReport
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.shared_workflow import build_shared_workflow_graph
from production_optimizer.orchestration.subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_c0_graph,
    build_s01_graph,
    build_s02_graph,
)

_TENANT_ID = "TENANT-CLI"
_ACTOR_ROLE = "owner"

# One control plane per process. Falls back to in-process stand-ins unless
# OPTIMIZER_DATABASE_DSN/OPTIMIZER_ARTIFACT_* point at reachable services --
# see `build_control_plane` for the OPTIMIZER_CONTROL_PLANE modes.
CONTROL_PLANE = build_control_plane(service_name="production-optimizer-apply")


def print_control_plane() -> None:
    state = "durable" if CONTROL_PLANE.durable else "NOT durable"
    print(f"[control-plane] {state}")
    for note in CONTROL_PLANE.notes:
        print(f"[control-plane]   {note}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full real pipeline (A1 -> A2 -> A3 -> C0 -> S01..S07) "
            "against a real local repository."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("repo_path", type=Path, help="Path to the target codebase.")
    parser.add_argument("--feature-id", required=True, help="Feature to optimize (e.g. checkout).")
    parser.add_argument(
        "--metric",
        required=True,
        help=(
            "Primary metric_id to optimize (e.g. unit_command_result, "
            "p95_latency_ms). Only meaningful if something in A2's real evidence "
            "collection actually produces that metric_id -- see --metric's help "
            "text in optimize.py for the same caveat."
        ),
    )
    parser.add_argument("--direction", required=True, choices=["minimize", "maximize", "target"])
    parser.add_argument("--target", type=float, required=True, help="Target value for --metric.")
    parser.add_argument("--unit", required=True, help="Unit for --metric (e.g. ms, exit_code).")
    parser.add_argument(
        "--command-id",
        required=True,
        help="Command to measure for this workload (e.g. pytest, ruff, cargo test).",
    )
    parser.add_argument("--workload-id", default=None, help="Default: <feature-id>-workload.")
    parser.add_argument("--environment-id", default="local-dev")
    parser.add_argument("--case-id", default="OPT-APPLY-1")
    parser.add_argument("--actor-id", default="cli-user")
    parser.add_argument(
        "--stop-after-stage",
        choices=["c0", "s01", "s02"],
        default=None,
        help=(
            "Stop once the named stage seals its output, instead of continuing through "
            "S07. 'c0' stops after ConvergedCase (no shared workflow at all). 's01' stops "
            "after SelectedSolution. 's02' stops after ExecutionPlan/TaskList/"
            "PlanQualityReport, before S03 would touch a real isolated workspace or spend "
            "another real model call on code editing. Default: run the full pipeline "
            "through S07, same as before this flag existed."
        ),
    )
    return parser.parse_args(argv)


def build_payload(args: argparse.Namespace) -> ManualCasePayload:
    return ManualCasePayload(
        local_path=str(args.repo_path),
        allowed_root=str(args.repo_path),
        feature_id=args.feature_id,
        metric_id=args.metric,
        direction=args.direction,
        target=args.target,
        unit=args.unit,
        workload_id=args.workload_id or f"{args.feature_id}-workload",
        environment_id=args.environment_id,
        command_id=args.command_id,
        actor_id=args.actor_id,
        actor_role=_ACTOR_ROLE,
    )


def seed_payload(store: Any, case_id: str, payload: ManualCasePayload) -> ArtifactRef:
    """Write the intake payload through the real `ArtifactStore` port.

    `seed_json` is a `MemoryArtifactStore`-only helper, so using it here
    made every entrypoint silently unusable with a durable store.
    """

    content = canonical_json(payload)
    digest = sha256_digest(content)
    stored = store.put_json(
        tenant_id=_TENANT_ID,
        content=content,
        content_digest=digest,
        idempotency_key=f"{case_id}:intake:ManualCasePayload",
    )
    return ArtifactRef(
        artifact_type="ManualCasePayload",
        schema_version="1.0",
        artifact_id="payload",
        content_digest=digest,
        uri=stored.uri,
    )


def _auto_resume_until_terminal(
    graph: Any, state: dict[str, Any], *, actor_id: str, thread_id: str, case_id: str
) -> dict[str, Any]:
    """Drive a compiled graph to a real terminal state, auto-approving every
    real human-in-the-loop halt as this CLI's own owner -- mirrors
    `application.resume.resume_case`'s own real authorization path, just
    always choosing "approve" (the one decision an unattended CLI run can
    make on its own owner's behalf) instead of prompting a human."""

    attempt = 0
    while state.get("pending_interrupt") is not None:
        attempt += 1
        interrupt = state["pending_interrupt"]
        decision = (
            "approve"
            if "approve" in interrupt.allowed_decisions
            else (interrupt.allowed_decisions[0])
        )
        print(
            f"  [auto-resume #{attempt}] {interrupt.stage} halted for "
            f"{interrupt.allowed_decisions} -- auto-deciding {decision!r} as this CLI's owner"
        )
        now = datetime.now(UTC)
        command = ResumeInterruptCommand(
            command_id=f"{case_id}-resume-{attempt}",
            tenant_id=_TENANT_ID,
            case_id=case_id,
            thread_id=thread_id,
            interrupt_id=interrupt.interrupt_id,
            actor_id=actor_id,
            actor_roles={interrupt.required_actor_role},
            decision=decision,
            artifact_digest=interrupt.artifact_digest,
            policy_version=interrupt.policy_version,
            issued_at=now,
        )
        actor = ActorContext(
            actor_id=actor_id,
            tenant_id=_TENANT_ID,
            roles={interrupt.required_actor_role},
            authenticated_at=now,
        )
        state = resume_case(
            graph=graph,
            state=cast("OptimizationState", state),
            command=command,
            actor=actor,
            now=now,
        )
    return state


def _run_s01_and_s02_only(
    state: dict[str, Any],
    ports: NodePorts,
    *,
    store: Any,
    args: argparse.Namespace,
    thread_id: str,
) -> None:
    """`--stop-after-stage s01`/`s02`: run S01 and (optionally) S02 as their
    own standalone compiled graphs -- deliberately NOT
    `build_shared_workflow_graph`, which wires the whole S01->...->S07
    PhaseLoop as one graph so S06's REVERT can jump back into S01 across
    stage boundaries. That cross-stage loop is irrelevant here (S03-S07
    never run in this path), and inspecting each stage's own graph keeps
    this path from ever touching S03's real isolated workspace or spending
    a real model call on code editing -- exactly the point of stopping
    early."""

    print("=== S01: Rank & Select ===")
    s01_graph = build_s01_graph(build_s01_runtime(ports=ports))
    state = s01_graph.invoke(state)
    state = _auto_resume_until_terminal(
        s01_graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
    )
    s01_routes = {k: v for k, v in state.get("node_routes", {}).items() if k.startswith("S01")}
    print(f"node_routes (S01): {s01_routes}")

    selected_ref = ref_by_type(state, "SelectedSolution")
    if selected_ref is None:
        print("S01 did not reach a sealed SelectedSolution -- see node_routes above.")
        return
    selected = read_model(store, _TENANT_ID, selected_ref, SelectedSolution)
    print(
        f"SelectedSolution: strategy_id={selected.strategy_id} "
        f"approval={selected.approval.decision}"
    )
    print()

    if args.stop_after_stage == "s01":
        print("Stopping after S01 per --stop-after-stage=s01.")
        return

    print("=== S02: Plan & Task List ===")
    s02_graph = build_s02_graph(
        build_s02_runtime(ports=ports), checkpointer=CONTROL_PLANE.checkpointer
    )
    state = s02_graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
    state = _auto_resume_until_terminal(
        s02_graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
    )
    s02_routes = {k: v for k, v in state.get("node_routes", {}).items() if k.startswith("S02")}
    print(f"node_routes (S02): {s02_routes}")

    quality_ref = ref_by_type(state, "PlanQualityReport")
    if quality_ref is not None:
        quality = read_model(store, _TENANT_ID, quality_ref, PlanQualityReport)
        print(f"PlanQualityReport.passed = {quality.passed}")
        for result in quality.results:
            marker = "OK" if result.passed else "FAIL"
            print(
                f"  [{marker}] {result.dimension}" + (f": {result.detail}" if result.detail else "")
            )

    plan_ref = ref_by_type(state, "ExecutionPlan")
    task_list_ref = ref_by_type(state, "TaskList")
    if plan_ref is None or task_list_ref is None:
        print(
            "S02 did not reach a sealed ExecutionPlan/TaskList -- see node_routes/"
            "PlanQualityReport above."
        )
        return
    plan = read_model(store, _TENANT_ID, plan_ref, ExecutionPlan)
    task_list = read_model(store, _TENANT_ID, task_list_ref, TaskList)
    print()
    print(f"ExecutionPlan: {len(plan.phases)} phase(s)")
    for phase in plan.phases:
        print(
            f"  - {phase.phase_id} (seq={phase.sequence}, kind={phase.phase_kind}): "
            f"{phase.treatment.variable} {phase.treatment.before} -> {phase.treatment.after}"
        )
    print(f"TaskList: {len(task_list.tasks)} task(s)")
    for task in task_list.tasks:
        print(f"  - {task.task_id} (phase={task.phase_id}): {task.objective}")
    print("Stopping after S02 per --stop-after-stage=s02.")


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
        artifacts=store,
        intents=CONTROL_PLANE.intents,
        telemetry=CONTROL_PLANE.telemetry,
        policy=CONTROL_PLANE.policy,
    )
    state = build_a1_graph(build_a1_runtime(ports=a1_ports)).invoke(
        {
            "case_id": args.case_id,
            "thread_id": thread_id,
            "tenant_id": _TENANT_ID,
            "entrypoint": "manual",
            "lane": "manual",
            "baseline_mode": "active_collection",
            "actor_context": ActorContext(
                actor_id=args.actor_id,
                tenant_id=_TENANT_ID,
                roles={"owner"},
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

    print("=== A2: collecting real evidence (pytest/ruff/mypy via LocalWorkerBroker) ===")
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
    )
    try:
        a2_ports = NodePorts(
            artifacts=store,
            intents=CONTROL_PLANE.intents,
            policy=CONTROL_PLANE.policy,
            telemetry=CONTROL_PLANE.telemetry,
            workers=broker,
        )
        state = build_a2_graph(build_a2_runtime(ports=a2_ports)).invoke(state)
    finally:
        broker.close()

    baseline_ref = ref_by_type(state, "BaselineSnapshot")
    if baseline_ref is None:
        print(f"A2 did not reach a sealed BaselineSnapshot (routes: {state.get('node_routes')}).")
        return
    baseline = read_model(store, _TENANT_ID, baseline_ref, BaselineSnapshot)
    print("Real metric aggregates collected:")
    for aggregate in baseline.aggregates:
        print(f"  - {aggregate.metric_id}: mean={aggregate.mean} unit={aggregate.unit}")
    print()

    print("=== A3: analyzing evidence and proposing strategies ===")
    model_provider, model_id = select_model_provider(
        tenant_id=_TENANT_ID,
        thread_id=thread_id,
        deferrals=CONTROL_PLANE.model_deferrals,
    )
    a3_ports = NodePorts(
        artifacts=store,
        intents=CONTROL_PLANE.intents,
        policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry,
        model=model_provider,
        model_id=model_id,
    )
    state = build_a3_graph(
        build_a3_runtime(ports=a3_ports), checkpointer=CONTROL_PLANE.checkpointer
    ).invoke(state, config={"configurable": {"thread_id": thread_id}})
    if ref_by_type(state, "SolutionPortfolio") is None:
        print(f"A3 did not reach a sealed SolutionPortfolio (routes: {state.get('node_routes')}).")
        return
    print(
        f"node_routes (A3): "
        f"{ {k: v for k, v in state.get('node_routes', {}).items() if k.startswith('A3')} }"
    )
    print()

    print("=== C0: converging the case ===")
    c0_ports = NodePorts(
        artifacts=store,
        intents=CONTROL_PLANE.intents,
        policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry,
    )
    state = build_c0_graph(build_c0_runtime(ports=c0_ports)).invoke(state)
    converged_ref = ref_by_type(state, "ConvergedCase")
    if converged_ref is None:
        print(f"Case did not converge (routes: {state.get('node_routes')}).")
        return
    converged_case = read_model(store, _TENANT_ID, converged_ref, ConvergedCase)
    print(f"ConvergedCase sealed: {converged_case.artifact_id}")
    print()

    if isinstance(model_provider, LocalScriptedModelProvider):
        print(
            "No real LLM available (no ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY/"
            "DEEPSEEK_API_KEY and no reachable local Ollama server): stopping here.\n"
            "S02.30 (plan drafting) and S03.50 (code editing, depending on the selected "
            "strategy's risk tier) both need a model that can really read and reason about "
            "this repository's code -- LocalScriptedModelProvider's A3 stand-in is real and "
            "evidence-grounded, but its Treatment is intentionally descriptive, not literal "
            "source text, so no honest deterministic executor can turn it into a real patch. "
            "Set a real API key (see .env.example) or run a local Ollama server to continue "
            "past this point."
        )
        return

    if args.stop_after_stage == "c0":
        print("Stopping after C0 per --stop-after-stage=c0.")
        return

    workflow_ports = NodePorts(
        artifacts=store,
        intents=CONTROL_PLANE.intents,
        policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry,
        model=model_provider,
        model_id=model_id,
    )

    if args.stop_after_stage in ("s01", "s02"):
        _run_s01_and_s02_only(state, workflow_ports, store=store, args=args, thread_id=thread_id)
        return

    print("=== Shared workflow: S01 (Rank & Select) -> ... -> S07 (Report) ===")
    shared_registrations = {
        **build_s01_registrations(),
        **build_s02_registrations(),
        **build_s03_registrations(),
        **build_s04_registrations(),
        **build_s05_registrations(),
        **build_s06_registrations(),
        **build_s07_registrations(),
    }
    graph = build_shared_workflow_graph(NodeRuntime(shared_registrations, ports=workflow_ports))
    state = graph.invoke(state)
    state = _auto_resume_until_terminal(
        graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
    )

    shared_routes = {
        k: v
        for k, v in state.get("node_routes", {}).items()
        if k.split(".")[0] in {f"S0{n}" for n in range(1, 8)}
    }
    print(f"node_routes (shared workflow): {shared_routes}")
    print(f"phase repair attempts: {state.get('s03_revision_attempts', 0)}")
    print()

    decision_ref = ref_by_type(state, "Decision")
    if decision_ref is not None:
        decision = read_model(store, _TENANT_ID, decision_ref, Decision)
        print(f"S06 decision: {decision.outcome} -- {decision.rationale}")
        print()

    report_ref = ref_by_type(state, "OptimizationReport")
    if report_ref is not None:
        report = read_model(store, _TENANT_ID, report_ref, OptimizationReport)
        print("=== S07: OptimizationReport ===")
        print(report.narrative)
        print()
        print("Evidence:")
        for item in report.evidence:
            print(
                f"  - {item.criterion_id} ({item.metric_id}): {item.baseline_mean} -> "
                f"{item.treatment_mean} ({'met' if item.met else 'not met'})"
            )
        print(
            f"Source change: {report.source_change.changed_files} "
            f"(base revision {report.source_change.base_revision})"
        )
        print(
            f"Cost: {report.cost.model_input_tokens} in / {report.cost.model_output_tokens} out "
            f"tokens, {report.cost.phase_repair_attempts} repair attempt(s)"
        )
    elif state.get("pending_interrupt") is None:
        print(
            "Case did not reach a published OptimizationReport -- see node_routes above "
            "for where it stopped (revert/escalate/halt)."
        )


if __name__ == "__main__":
    try:
        main()
    except DeferredModelCallError as error:
        print(f"\n[DEFERRED] {error}")
        if error.deferral_id is not None:
            print(f"Deferred model call persisted: {error.deferral_id}")
        print(
            "All approved model providers are temporarily unavailable. In a durable "
            "deployment this would be persisted and resumed from the exact model node "
            "by the scheduler; this local CLI has no durable scheduler, so re-run the "
            "same command after the retry window or configure another approved provider."
        )
        raise SystemExit(75) from None
    except Exception as error:
        # `a3_handlers`/`s02_handlers` call `ports.model.complete()` bare, on
        # purpose (see `RetryingModelProvider`'s own docstring): a node must
        # not hide a dead provider. `RetryingModelProvider` already retries
        # every transient failure (rate limit, timeout, 5xx) with backoff
        # before this ever surfaces, so by the time it reaches here the
        # provider has been down for the whole retry window, not one bad
        # request. A raw traceback buries that one-line fact under 40 lines
        # of langgraph/tenacity/SDK internals -- print it plainly instead,
        # then exit non-zero (this is still a real failure, not a success).
        if is_retryable(error):
            print(
                f"\n[FATAL] The model provider kept failing after retries: "
                f"{type(error).__name__}: {error}"
            )
            print(
                "This is a transient upstream issue (the provider is overloaded, rate-limited, "
                "or timing out) -- not a bug in this pipeline. Nothing was corrupted: state for "
                "this run lives only in this process's memory, so re-running the command starts "
                "clean. Options: try again shortly, set a different *_MODEL_ID in .env for the "
                "same provider, or set a different provider's API key (see .env.example)."
            )
            raise SystemExit(1) from None
        raise
