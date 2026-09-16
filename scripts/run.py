"""Single CLI entrypoint for the whole business workflow, selected by `--tag`.

    python scripts/run.py <tag> <path-to-repo> [tag-specific args...]

Available tags:

  lane1        A1 -> A2 -> A3 -> C0 (real Lane A, same as `optimize.py`).
  lane1-plan   A1 -> A2 -> A3 -> C0 -> S01 -> S02, stops before S03 ever
               touches a real isolated workspace (same as
               `plan_to_tasklist.py`).
  full         A1 -> A2 -> A3 -> C0 -> S01 -> S02 -> PhaseLoop(S03<->S04<->
               S05<->S06) -> S07, the entire pipeline through a published
               `OptimizationReport` (same as `optimize_and_apply.py`).
  lane2        B1 automatic discovery only (B1.10 -> ... -> B1.96), against
               a `RegisteredSourceSet` seeded from `repo_path`. Deliberately
               does NOT chain into B2: B1.32-35's real metrics/logs/traces
               query adapters are not wired into any `NodePorts` yet (a
               deliberate scope decision, not a bug -- see CLAUDE.md's
               "Known Limitations"), so a real run over an arbitrary
               repository essentially always detects zero signals. Chaining
               into B2 would mean fabricating a signal B1 could not really
               find, which this script will not do. Run
               `pytest tests/contract/test_b2_production_handlers.py` to see
               B2's real logic exercised against a fabricated handoff
               instead.

This script does not replace `optimize.py`/`optimize_and_apply.py`/
`plan_to_tasklist.py` -- each remains runnable standalone. It exists so a
single command can pick which slice of the graph to run, instead of picking
which file to run.

Examples:

    python scripts/run.py lane1 fixtures/sample-repo --feature-id tests \\
        --metric unit_command_result --direction minimize --target 0 \\
        --unit exit_code --command-id pytest

    python scripts/run.py full fixtures/sample-repo --feature-id tests \\
        --metric unit_command_result --direction minimize --target 0 \\
        --unit exit_code --command-id pytest

    python scripts/run.py lane2 fixtures/sample-repo
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from _lane1_common import (
    add_model_runtime_args,
    build_control_plane,
    model_selection_kwargs_from_args,
    read_model,
    ref_by_type,
    select_model_provider,
)

from production_optimizer.adapters.production import report_model_provider_error
from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import (
    NodePorts,
    NodeRuntime,
    build_a1_runtime,
    build_a2_runtime,
    build_a3_runtime,
    build_b1_runtime,
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
from production_optimizer.contracts.a2 import BaselineSnapshot, EvidenceQualityReport
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    FindingSet,
    RevisionDirective,
    SolutionPortfolio,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.b1 import DetectionReport, RegisteredSource, RegisteredSourceSet
from production_optimizer.contracts.c0 import ConvergedCase, ConvergenceDecision
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.contracts.evaluation import ContainerCommandSpec, EvaluationSpec
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
    build_b1_graph,
    build_c0_graph,
    build_s01_graph,
    build_s02_graph,
)

_TENANT_ID = "TENANT-CLI"


# One control plane per process. Falls back to in-process stand-ins unless
# OPTIMIZER_DATABASE_DSN/OPTIMIZER_ARTIFACT_* point at reachable services --
# see `build_control_plane` for the OPTIMIZER_CONTROL_PLANE modes.
CONTROL_PLANE = build_control_plane(service_name="production-optimizer-run")


def _print_control_plane() -> None:
    state = "durable" if CONTROL_PLANE.durable else "NOT durable"
    print(f"[control-plane] {state}")
    for note in CONTROL_PLANE.notes:
        print(f"[control-plane]   {note}")


def _auto_resume_until_terminal(
    graph: Any, state: dict[str, Any], *, actor_id: str, thread_id: str, case_id: str
) -> dict[str, Any]:
    """Drive a compiled graph to a real terminal state, auto-approving every
    real human-in-the-loop halt as this CLI's own owner -- the one decision
    an unattended CLI run can make on its own owner's behalf, instead of
    prompting a human. Mirrors the identically-shaped helper duplicated in
    `optimize.py`/`optimize_and_apply.py`/`plan_to_tasklist.py`."""

    attempt = 0
    while state.get("pending_interrupt") is not None:
        attempt += 1
        interrupt = state["pending_interrupt"]
        decision = (
            "approve"
            if "approve" in interrupt.allowed_decisions
            else interrupt.allowed_decisions[0]
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


# --------------------------------------------------------------------------
# Lane A (lane1 / lane1-plan / full): shared CLI surface + A1 seed payload.
# --------------------------------------------------------------------------


def _add_lane1_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repo_path", type=Path, help="Path to the target codebase.")
    parser.add_argument("--feature-id", required=True, help="Feature to optimize (e.g. checkout).")
    parser.add_argument(
        "--metric",
        required=True,
        help=(
            "Primary metric_id to optimize (e.g. p95_latency_ms, unit_command_result). "
            "Only command-exit-code-based metrics (unit_command_result, lint_command_result) "
            "are reliably collectible today -- see CLAUDE.md's Known Limitations."
        ),
    )
    parser.add_argument("--direction", required=True, choices=["minimize", "maximize", "target"])
    parser.add_argument("--target", type=float, required=True, help="Target value for --metric.")
    parser.add_argument("--unit", required=True, help="Unit for --metric (e.g. ms, exit_code).")
    parser.add_argument(
        "--command-id",
        default=None,
        help=(
            "Command to measure for this workload (e.g. pytest, ruff, cargo test). "
            "Required for --execution-profile legacy_discovery; unused for docker_compose."
        ),
    )
    parser.add_argument("--workload-id", default=None, help="Default: <feature-id>-workload.")
    parser.add_argument("--environment-id", default="local-dev")
    parser.add_argument("--case-id", default="OPT-CLI-1")
    parser.add_argument("--actor-id", default="cli-user")
    parser.add_argument(
        "--guardrail-metric-id",
        default=None,
        help=(
            "Metric A1.62's correctness guardrail checks (default: 'unit_command_result'). "
            "For --execution-profile docker_compose this MUST name one of --eval-metrics."
        ),
    )
    parser.add_argument("--maximum-worker-seconds", type=int, default=None)
    parser.add_argument("--deadline-seconds", type=int, default=None)
    parser.add_argument(
        "--trace-node-outputs",
        action="store_true",
        help="Print each business node's checkpoint output as JSON as it runs.",
    )
    add_model_runtime_args(parser)

    compose = parser.add_argument_group(
        "docker_compose execution profile",
        "Only used when --execution-profile docker_compose is set.",
    )
    parser.add_argument(
        "--execution-profile",
        choices=["legacy_discovery", "docker_compose"],
        default="legacy_discovery",
    )
    compose.add_argument("--compose-file", default=None)
    compose.add_argument("--eval-id", default=None)
    compose.add_argument("--eval-service", default=None)
    compose.add_argument("--eval-command", nargs="+", default=None)
    compose.add_argument("--eval-working-dir", default=None)
    compose.add_argument("--eval-metrics", default=None)
    compose.add_argument("--eval-repetitions", type=int, default=None)
    compose.add_argument("--eval-warmup", type=int, default=None)
    compose.add_argument("--eval-timeout", type=int, default=300)
    compose.add_argument("--application-services", default=None)


def _validate_lane1_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.execution_profile == "legacy_discovery" and not args.command_id:
        parser.error("--command-id is required for --execution-profile legacy_discovery")
    if args.execution_profile == "docker_compose":
        missing = [
            name
            for name, value in (
                ("--compose-file", args.compose_file),
                ("--eval-service", args.eval_service),
                ("--eval-command", args.eval_command),
                ("--eval-metrics", args.eval_metrics),
            )
            if not value
        ]
        if missing:
            parser.error("--execution-profile docker_compose also requires: " + ", ".join(missing))


def _build_payload(args: argparse.Namespace) -> ManualCasePayload:
    compose_kwargs: dict[str, Any] = {}
    if args.execution_profile == "docker_compose":
        eval_metrics = {item.strip() for item in args.eval_metrics.split(",") if item.strip()}
        application_services = (
            [item.strip() for item in args.application_services.split(",") if item.strip()]
            if args.application_services
            else [args.eval_service]
        )
        evaluation = EvaluationSpec(
            evaluation_id=args.eval_id or f"{args.feature_id}-evaluation",
            command=ContainerCommandSpec(
                service=args.eval_service,
                argv=args.eval_command,
                working_directory=args.eval_working_dir,
                timeout_seconds=args.eval_timeout,
            ),
            **({"repetitions": args.eval_repetitions} if args.eval_repetitions is not None else {}),
            **({"warmup_runs": args.eval_warmup} if args.eval_warmup is not None else {}),
            expected_metric_ids=eval_metrics,
        )
        compose_kwargs = {
            "compose_file": args.compose_file,
            "application_services": application_services,
            "evaluations": [evaluation],
        }
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
        execution_profile=args.execution_profile,
        guardrail_metric_id=args.guardrail_metric_id,
        maximum_worker_seconds=args.maximum_worker_seconds,
        deadline_seconds=args.deadline_seconds,
        actor_id=args.actor_id,
        actor_role="owner",  # auto-approves at A1.90 -- this CLI has no interrupt-resume flow
        **compose_kwargs,
    )


def _seed_payload(store: Any, case_id: str, payload: ManualCasePayload) -> ArtifactRef:
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


def _print_a3_finding_rejection_diagnostics(store: Any, state: dict[str, Any]) -> None:
    from production_optimizer.contracts.a3 import FindingDraftSet

    if state.get("node_routes", {}).get("A3.51") != "rejected":
        return
    draft_ref = ref_by_type(state, "FindingDraftSet")
    if draft_ref is None:
        return
    print("A3 finding gate rejected before FindingSet:")
    drafts = read_model(store, _TENANT_ID, draft_ref, FindingDraftSet)
    for failure in drafts.generation_failures:
        print(f"  - generation failure: {failure}")
    print()


def _run_lane1(
    args: argparse.Namespace, *, thread_id: str, store: Any
) -> tuple[dict[str, Any], Any, str]:
    """Runs A1 -> A2 -> A3 -> C0. Returns (state, model_provider, model_id)
    so callers that continue into the shared workflow can reuse the same
    provider instead of picking one twice."""

    print(f"Target codebase: {args.repo_path}")
    print(f"Feature: {args.feature_id}")
    print(f"Criterion: {args.metric} {args.direction} {args.target}{args.unit}")
    print()

    payload_ref = _seed_payload(store, args.case_id, _build_payload(args))

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

    pending = state.get("pending_interrupt")
    if pending is not None:
        print(
            f"A1 stopped for approval/clarification at {pending.stage}: {pending.allowed_decisions}"
        )
        print("This CLI has no interrupt-resume flow -- adjust --feature-id/--metric and rerun.")
        raise SystemExit(0)
    if state.get("request_ref") is None:
        print(f"A1 did not produce a request (routes: {state.get('node_routes')}).")
        raise SystemExit(0)
    print(f"A1 completed: {sorted(state.get('completed_nodes', []))}")
    print()

    print("=== A2: collecting real evidence (pytest/ruff via LocalWorkerBroker) ===")
    model_provider, model_id = select_model_provider(
        tenant_id=_TENANT_ID, thread_id=thread_id, **model_selection_kwargs_from_args(args)
    )
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
            model=model_provider,
            model_id=model_id,
        )
        a2_graph = build_a2_graph(
            build_a2_runtime(ports=a2_ports), checkpointer=CONTROL_PLANE.checkpointer
        )
        state = a2_graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
        state = _auto_resume_until_terminal(
            a2_graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
        )
    finally:
        broker.close()

    baseline_ref = state.get("baseline_ref") or ref_by_type(state, "BaselineSnapshot")
    if baseline_ref is None:
        print("A2 did not reach a sealed BaselineSnapshot.")
        quality_ref = ref_by_type(state, "EvidenceQualityReport")
        if quality_ref is not None:
            quality = read_model(store, _TENANT_ID, quality_ref, EvidenceQualityReport)
            print(f"EvidenceQualityReport.passed = {quality.passed}")
            for failure in quality.sample_failures:
                print(f"  - {failure}")
        print(f"completed_nodes: {sorted(state.get('completed_nodes', []))}")
        raise SystemExit(0)

    baseline = read_model(store, _TENANT_ID, baseline_ref, BaselineSnapshot)
    print("Real metric aggregates collected:")
    for aggregate in baseline.aggregates:
        print(f"  - {aggregate.metric_id}: mean={aggregate.mean} unit={aggregate.unit}")
    print()

    print("=== A3: analyzing evidence and proposing strategies ===")
    a3_ports = NodePorts(
        artifacts=store,
        intents=CONTROL_PLANE.intents,
        policy=CONTROL_PLANE.policy,
        model=model_provider,
        model_id=model_id,
    )
    state = build_a3_graph(
        build_a3_runtime(ports=a3_ports), checkpointer=CONTROL_PLANE.checkpointer
    ).invoke(state, config={"configurable": {"thread_id": thread_id}})
    a3_routes = {k: v for k, v in state.get("node_routes", {}).items() if k.startswith("A3")}
    print(f"node_routes (A3): {a3_routes}")
    print()

    finding_ref = ref_by_type(state, "FindingSet")
    if finding_ref is not None:
        findings = read_model(store, _TENANT_ID, finding_ref, FindingSet)
        print(f"Findings sealed: {len(findings.findings)}")
    else:
        _print_a3_finding_rejection_diagnostics(store, state)

    quality_ref = ref_by_type(state, "A3QualityReport")
    if quality_ref is not None:
        quality = read_model(store, _TENANT_ID, quality_ref, A3QualityReport)
        print(f"A3 quality gate passed: {quality.passed}")
        if not quality.passed:
            for gate_results in (
                quality.finding_gate_results,
                quality.strategy_gate_results,
                quality.cause_maturity_gate_results,
                quality.portfolio_gate_results,
            ):
                for gate in gate_results:
                    if not gate.passed:
                        detail = f": {gate.detail}" if gate.detail else ""
                        print(f"  - [FAIL] {gate.dimension}{detail}")
        print()

    portfolio_ref = state.get("solution_portfolio_ref") or ref_by_type(state, "SolutionPortfolio")
    if portfolio_ref is not None:
        portfolio = read_model(store, _TENANT_ID, portfolio_ref, SolutionPortfolio)
        print(f"Solution strategies: {len(portfolio.strategies)}")
        for strategy in portfolio.strategies:
            marker = "ELIGIBLE" if strategy.eligible else "ineligible"
            print(f"  - [{marker}] {strategy.strategy_id}: {strategy.title}")
    else:
        print("A3 did not reach a sealed SolutionPortfolio.")
        directive_refs = [
            ref
            for ref in state.get("artifact_refs", [])
            if ref.artifact_type == "RevisionDirective"
        ]
        if directive_refs:
            latest = max(
                (read_model(store, _TENANT_ID, ref, RevisionDirective) for ref in directive_refs),
                key=lambda directive: directive.attempt_number,
            )
            print(f"  (stopped in the revision loop after attempt {latest.attempt_number})")
            print(f"  reason: {latest.reason}")
        raise SystemExit(0)

    print()
    print("=== C0: converging the case ===")
    c0_ports = NodePorts(
        artifacts=store,
        intents=CONTROL_PLANE.intents,
        policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry,
    )
    state = build_c0_graph(build_c0_runtime(ports=c0_ports)).invoke(state)
    c0_routes = {k: v for k, v in state.get("node_routes", {}).items() if k.startswith("C0")}
    print(f"node_routes (C0): {c0_routes}")

    decision_ref = ref_by_type(state, "ConvergenceDecision")
    if decision_ref is not None:
        decision = read_model(store, _TENANT_ID, decision_ref, ConvergenceDecision)
        print(f"Converged: {decision.converged}")
        if decision.reasons:
            print(f"Reasons: {decision.reasons}")

    converged_ref = ref_by_type(state, "ConvergedCase")
    if converged_ref is not None:
        converged_case = read_model(store, _TENANT_ID, converged_ref, ConvergedCase)
        print(f"ConvergedCase sealed: {converged_case.artifact_id}")
    else:
        print("Case did not converge -- see reasons above.")
        raise SystemExit(0)

    return state, model_provider, model_id


def _run_shared_workflow(
    state: dict[str, Any],
    args: argparse.Namespace,
    *,
    store: Any,
    thread_id: str,
    model_provider: Any,
    model_id: str,
    stop_after_stage: str | None,
) -> None:
    """Runs S01 -> S02 -> (PhaseLoop S03<->S04<->S05<->S06) -> S07, or stops
    early per `stop_after_stage` ('s01'/'s02'). `None` runs through S07."""

    workflow_ports = NodePorts(
        artifacts=store,
        intents=CONTROL_PLANE.intents,
        policy=CONTROL_PLANE.policy,
        telemetry=CONTROL_PLANE.telemetry,
        model=model_provider,
        model_id=model_id,
    )

    if stop_after_stage in ("s01", "s02"):
        print("=== S01: Rank & Select ===")
        s01_graph = build_s01_graph(build_s01_runtime(ports=workflow_ports))
        state = s01_graph.invoke(state)
        state = _auto_resume_until_terminal(
            s01_graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
        )
        selected_ref = ref_by_type(state, "SelectedSolution")
        if selected_ref is None:
            print("S01 did not reach a sealed SelectedSolution -- see node_routes above.")
            return
        selected = read_model(store, _TENANT_ID, selected_ref, SelectedSolution)
        print(f"SelectedSolution: strategy_id={selected.strategy_id}")
        print()
        if stop_after_stage == "s01":
            print("Stopping after S01 per tag lane1-plan (--stop-at s01).")
            return

        print("=== S02: Plan & Task List ===")
        s02_graph = build_s02_graph(
            build_s02_runtime(ports=workflow_ports), checkpointer=CONTROL_PLANE.checkpointer
        )
        state = s02_graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
        state = _auto_resume_until_terminal(
            s02_graph, state, actor_id=args.actor_id, thread_id=thread_id, case_id=args.case_id
        )
        quality_ref = ref_by_type(state, "PlanQualityReport")
        if quality_ref is not None:
            quality = read_model(store, _TENANT_ID, quality_ref, PlanQualityReport)
            print(f"PlanQualityReport.passed = {quality.passed}")
        plan_ref = ref_by_type(state, "ExecutionPlan")
        task_list_ref = ref_by_type(state, "TaskList")
        if plan_ref is None or task_list_ref is None:
            print("S02 did not reach a sealed ExecutionPlan/TaskList.")
            return
        plan = read_model(store, _TENANT_ID, plan_ref, ExecutionPlan)
        task_list = read_model(store, _TENANT_ID, task_list_ref, TaskList)
        print(f"ExecutionPlan: {len(plan.phases)} phase(s)")
        print(f"TaskList: {len(task_list.tasks)} task(s)")
        return

    if model_provider.__class__.__name__ == "LocalScriptedModelProvider":
        print(
            "No real LLM available: stopping after C0. S02.30/S03.50 both need a model that "
            "can really read and reason about this repository's code -- set a real API key "
            "(see .env.example) or run a local Ollama server to continue past this point."
        )
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
        print(
            f"Cost: {report.cost.model_input_tokens} in / {report.cost.model_output_tokens} out "
            f"tokens, {report.cost.phase_repair_attempts} repair attempt(s)"
        )
    elif state.get("pending_interrupt") is None:
        print("Case did not reach a published OptimizationReport -- see node_routes above.")


# --------------------------------------------------------------------------
# Lane B discovery (lane2): B1 only, seeded from a RegisteredSourceSet.
# --------------------------------------------------------------------------


def _add_lane2_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repo_path", type=Path, help="Path to the target codebase to scan.")
    parser.add_argument("--feature-id", default="discovered-feature")
    parser.add_argument("--case-id", default="OPT-DISCOVERY-1")
    parser.add_argument("--actor-id", default="cli-user")


def _run_lane2(args: argparse.Namespace, *, thread_id: str, store: Any) -> None:
    print(f"Target codebase: {args.repo_path}")
    print()

    registry_payload = RegisteredSourceSet(
        artifact_id="seed-registry",
        tenant_id=_TENANT_ID,
        case_id=args.case_id,
        created_at=datetime.now(UTC),
        producer=ProducerIdentity(name="run.py", version="1.0"),
        content_digest=f"sha256:{'0' * 64}",
        parent_digests=[],
        sources=[
            RegisteredSource(
                source_id=f"src-{uuid.uuid4().hex[:8]}",
                feature_id=args.feature_id,
                repository_id=args.repo_path.name,
                local_path=str(args.repo_path),
            )
        ],
    )
    sealed = registry_payload.model_copy(
        update={"content_digest": sha256_digest(canonical_json(registry_payload))}
    )
    content = canonical_json(sealed)
    digest = sha256_digest(content)
    stored = store.put_json(
        tenant_id=_TENANT_ID,
        content=content,
        content_digest=digest,
        idempotency_key=f"{args.case_id}:seed-registry",
    )
    # `MemoryArtifactStore.put_json` always tags its return as a generic
    # "JsonArtifact" ref -- B1.20 requires a ref whose `artifact_type` is
    # actually "RegisteredSourceSet" to find this seed (see
    # `optimize.py::_seed_payload`'s identical pattern for `ManualCasePayload`).
    registry_ref = ArtifactRef(
        artifact_type="RegisteredSourceSet",
        schema_version="1.0",
        artifact_id="seed-registry",
        content_digest=digest,
        uri=stored.uri,
    )

    print("=== B1: automatic discovery ===")
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
    )
    try:
        b1_ports = NodePorts(
            artifacts=store,
            intents=CONTROL_PLANE.intents,
            policy=CONTROL_PLANE.policy,
            telemetry=CONTROL_PLANE.telemetry,
            workers=broker,
        )
        runtime = build_b1_runtime(ports=b1_ports)
        state = build_b1_graph(runtime).invoke(
            {
                "case_id": args.case_id,
                "thread_id": thread_id,
                "tenant_id": _TENANT_ID,
                "entrypoint": "discovery",
                "lane": "automatic",
                "baseline_mode": "historical_recovery",
                "artifact_refs": [registry_ref],
            }
        )
    finally:
        broker.close()

    b1_routes = {k: v for k, v in state.get("node_routes", {}).items() if k.startswith("B1")}
    print(f"node_routes (B1): {b1_routes}")
    print()

    report_ref = ref_by_type(state, "DetectionReport")
    if report_ref is None:
        print("B1 did not reach a sealed DetectionReport -- see node_routes above.")
        return

    report = read_model(store, _TENANT_ID, report_ref, DetectionReport)
    print(
        f"DetectionReport: {len(report.signals)} signal(s), "
        f"{len(report.run_groups)} run group(s)"
    )
    for signal in report.signals:
        print(f"  - {signal.signal_id} ({signal.signal_kind}): {signal.description}")
    for exclusion in report.exclusions:
        print(f"  excluded: {exclusion}")
    if not report.signals:
        print(
            "  (expected: B1.32-35's real metrics/logs/traces query adapters are not wired "
            "into any NodePorts yet -- a deliberate scope decision, not a bug. See CLAUDE.md's "
            "\"Known Limitations\" -> \"B1.32-35 real query adapters\". Without a real detector, "
            "B1 has nothing to qualify, so this run stops here rather than fabricating an "
            "opportunity to hand to B2.)"
        )

    opportunity_ref = ref_by_type(state, "QualifiedOpportunity")
    if opportunity_ref is not None:
        print(f"QualifiedOpportunity sealed: {opportunity_ref.artifact_id}")
        print(
            "(B2 is not chained automatically -- see "
            "tests/contract/test_b2_production_handlers.py for B2's real handler logic)"
        )


# --------------------------------------------------------------------------
# CLI wiring.
# --------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one slice of the business workflow, selected by tag.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="tag", required=True)

    lane1 = subparsers.add_parser("lane1", help="A1 -> A2 -> A3 -> C0.")
    _add_lane1_args(lane1)

    lane1_plan = subparsers.add_parser(
        "lane1-plan", help="A1 -> A2 -> A3 -> C0 -> S01 -> S02 (stops before S03)."
    )
    _add_lane1_args(lane1_plan)

    full = subparsers.add_parser(
        "full", help="A1 -> A2 -> A3 -> C0 -> S01 -> ... -> S07 (entire pipeline)."
    )
    _add_lane1_args(full)

    lane2 = subparsers.add_parser("lane2", help="B1 automatic discovery only.")
    _add_lane2_args(lane2)

    args = parser.parse_args(argv)
    if args.tag in ("lane1", "lane1-plan", "full"):
        _validate_lane1_args(parser, args)
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    if getattr(args, "trace_node_outputs", False):
        os.environ["OPTIMIZER_TRACE_NODE_OUTPUTS"] = "1"
    args.repo_path = args.repo_path.resolve()
    if not args.repo_path.exists():
        raise SystemExit(f"repo path does not exist: {args.repo_path}")

    _print_control_plane()
    thread_id = f"THREAD-{args.case_id}"
    store = CONTROL_PLANE.artifacts

    if args.tag == "lane1":
        _run_lane1(args, thread_id=thread_id, store=store)
        return

    if args.tag == "lane1-plan":
        state, model_provider, model_id = _run_lane1(args, thread_id=thread_id, store=store)
        print()
        _run_shared_workflow(
            state,
            args,
            store=store,
            thread_id=thread_id,
            model_provider=model_provider,
            model_id=model_id,
            stop_after_stage="s02",
        )
        return

    if args.tag == "full":
        state, model_provider, model_id = _run_lane1(args, thread_id=thread_id, store=store)
        print()
        _run_shared_workflow(
            state,
            args,
            store=store,
            thread_id=thread_id,
            model_provider=model_provider,
            model_id=model_id,
            stop_after_stage=None,
        )
        return

    if args.tag == "lane2":
        _run_lane2(args, thread_id=thread_id, store=store)
        return

    raise AssertionError(f"unhandled tag: {args.tag}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # `a3_handlers`/`a2_handlers`/`s02_handlers` call `ports.model.complete()`
        # bare, on purpose: a node must not hide a dead provider.
        # `GenericModelProvider` already turns any SDK failure into
        # `ModelProviderError`; this just renders it as a clean one-line
        # explanation + exit code instead of a raw traceback. Anything else
        # is re-raised unchanged.
        raise SystemExit(report_model_provider_error(error)) from None
