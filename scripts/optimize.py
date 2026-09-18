"""CLI: point Lane 1 at a real codebase + feature + optimization criteria, and it runs.

    python scripts/optimize.py <path-to-repo> --feature-id checkout \
        --metric p95_latency_ms --direction minimize --target 180 --unit ms \
        --unit-command "pytest"

Input is deliberately structured, not free text: a feature to optimize plus
an explicit primary metric/direction/target/unit. A1 no longer attempts to
extract these from prose -- see `ManualCasePayload` (`contracts/a1.py`).

Runs the real, production A1 -> A2 -> A3 graphs end to end:
  - A1 seals the structured request into an `OptimizationRequest`
    (auto-approved: this CLI always acts as the request's own owner).
  - A2 genuinely executes the repository's own pytest/ruff commands via
    `LocalWorkerBroker` and collects real evidence from them.
  - A3 detects real guardrail/criterion signals against that evidence,
    drafts findings, judges them, and proposes strategies -- using a real
    `ModelProviderPort` if credentials are found (see `.env.example`:
    `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`/`DEEPSEEK_API_KEY`,
    or a locally reachable Ollama server), otherwise a clearly-labeled local
    stand-in so the whole pipeline still runs for free.

No repository is bundled by this CLI -- see `fixtures/sample-repo` for a
small pre-built one, or point this at your own project:

    python scripts/optimize.py fixtures/sample-repo --feature-id tests \
        --metric unit_command_result --direction minimize --target 0 --unit exit_code \
        --unit-command "pytest"

For a non-Python repository, A2's own convention detection (pytest/ruff/mypy)
finds nothing -- declare the real commands per kind instead:

    python scripts/optimize.py fixtures/go-checkout --feature-id checkout \
        --metric unit_command_result --direction minimize --target 0 --unit exit_code \
        --build-command "go build ./..." --unit-command "go test ./..."

For a repository that is not runnable directly on the host (needs a database,
a specific runtime, etc.), pass --execution-profile docker_compose instead:
A2 will build/up the given Compose file, exec the evaluation command inside
the named service for real, parse its stdout as the sealed `EvaluationOutput`
JSON protocol, then tear the project down -- see
`docs/detail/docker-compose-evaluation-refactor.md`. The evaluation command
MUST print exactly one JSON object shaped like `EvaluationOutput`
(contracts/evaluation.py): {"schema_version":"1.0","feature_id":"...",
"metrics":{...}} on stdout, with `feature_id` equal to --feature-id and every
--eval-metrics name present in "metrics".

    python scripts/optimize.py fixtures/external/realworld-go \
        --feature-id user-domain \
        --metric suite_runtime_ms --direction minimize --target 5000 --unit ms \
        --guardrail-metric-id correctness \
        --execution-profile docker_compose \
        --compose-file optimizer.compose.yaml \
        --eval-service testbed \
        --eval-command /workspace/scripts/optimizer-evaluate-users.sh \
        --eval-metrics suite_runtime_ms,correctness \
        --application-services testbed \
        --maximum-worker-seconds 600
"""

from __future__ import annotations

import argparse
import os
import sys
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
    build_a1_runtime,
    build_a2_runtime,
    build_a3_runtime,
    build_c0_runtime,
)
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.resume import resume_case
from production_optimizer.contracts.a1 import ManualCasePayload
from production_optimizer.contracts.a2 import BaselineSnapshot, EvidenceQualityReport
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    CitationResolutionReportSet,
    FindingDraftSet,
    FindingJudgementSet,
    FindingSet,
    ProblemSignalSet,
    RevisionDirective,
    SolutionPortfolio,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.c0 import ConvergedCase, ConvergenceDecision
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.commands import ResumeInterruptCommand
from production_optimizer.contracts.evaluation import ContainerCommandSpec, EvaluationSpec
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.contracts.state import OptimizationState
from production_optimizer.orchestration.subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_c0_graph,
)

_TENANT_ID = "TENANT-CLI"

# One control plane per process. Falls back to in-process stand-ins unless
# OPTIMIZER_DATABASE_DSN/OPTIMIZER_ARTIFACT_* point at reachable services --
# see `build_control_plane` for the OPTIMIZER_CONTROL_PLANE modes.
CONTROL_PLANE = build_control_plane(service_name="production-optimizer-cli")


def print_control_plane() -> None:
    state = "durable" if CONTROL_PLANE.durable else "NOT durable"
    print(f"[control-plane] {state}")
    for note in CONTROL_PLANE.notes:
        print(f"[control-plane]   {note}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Lane 1 (A1 -> A2 -> A3) against a real local repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("repo_path", type=Path, help="Path to the target codebase.")
    parser.add_argument("--feature-id", required=True, help="Feature to optimize (e.g. checkout).")
    parser.add_argument(
        "--metric",
        required=True,
        help=(
            "Primary metric_id to optimize (e.g. p95_latency_ms, unit_command_result). "
            "A real benchmark/telemetry collector must exist for it -- today's A2 only "
            "reliably collects command-exit-code-based evidence (unit_command_result, "
            "lint_command_result); a performance metric with no collector will "
            "legitimately produce no signal."
        ),
    )
    parser.add_argument("--direction", required=True, choices=["minimize", "maximize", "target"])
    parser.add_argument("--target", type=float, required=True, help="Target value for --metric.")
    parser.add_argument("--unit", required=True, help="Unit for --metric (e.g. ms, exit_code).")
    parser.add_argument(
        "--command-id",
        default=None,
        help=(
            "Deprecated alias for --unit-command (kept for existing scripts/muscle "
            "memory). Same 'unit' kind; error if both are given with different values."
        ),
    )
    parser.add_argument(
        "--build-command", default=None, help="Real 'go build ./...'-style build command."
    )
    parser.add_argument(
        "--lint-command", default=None, help="Real lint command (e.g. 'golangci-lint run')."
    )
    parser.add_argument(
        "--type-command", default=None, help="Real type-check command, if the language has one."
    )
    parser.add_argument(
        "--unit-command",
        default=None,
        help=(
            "Real unit-test command to measure for this workload (e.g. 'go test ./...', "
            "'cargo test'). At least one of --build/--lint/--type/--unit-command is "
            "required for --execution-profile legacy_discovery (A2's own convention "
            "detection only recognizes Python's pytest/ruff/mypy); all four are "
            "ignored/unused for docker_compose (the real command lives in "
            "--eval-command instead)."
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
            "Metric A1.62's correctness guardrail checks (default: 'unit_command_result', "
            "the legacy pytest/ruff exit-code shape). For --execution-profile docker_compose "
            "this MUST name one of --eval-metrics that actually encodes pass/fail "
            "(e.g. 'correctness') or A1.80 will halt: the default won't be among the "
            "evaluator's declared metric_ids."
        ),
    )
    parser.add_argument(
        "--maximum-worker-seconds",
        type=int,
        default=None,
        help=(
            "Wall-clock budget for A2's worker execution (default: 180s). For "
            "docker_compose this covers build+up+all evaluation repetitions+down -- "
            "raise it (e.g. 600) for anything that builds a real image or waits on a "
            "database healthcheck."
        ),
    )
    parser.add_argument("--deadline-seconds", type=int, default=None, help="Default: 300s.")
    parser.add_argument(
        "--trace-node-outputs",
        action="store_true",
        help=(
            "Print each LangGraph business node's checkpoint output as JSON as it runs. "
            "Equivalent to setting OPTIMIZER_TRACE_NODE_OUTPUTS=1."
        ),
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
        help=(
            "legacy_discovery (default): A2 runs the declared/detected commands "
            "directly on the host via LocalWorkerBroker. docker_compose: A2 "
            "builds/starts --compose-file for real and execs --eval-command inside "
            "--eval-service."
        ),
    )
    compose.add_argument(
        "--compose-file",
        default=None,
        help="Path to the Compose file, relative to repo_path (e.g. optimizer.compose.yaml).",
    )
    compose.add_argument(
        "--eval-id",
        default=None,
        help="Evaluation identifier. Default: '<feature-id>-evaluation'.",
    )
    compose.add_argument(
        "--eval-service",
        default=None,
        help="Compose service to `exec` the evaluation command inside.",
    )
    compose.add_argument(
        "--eval-command",
        nargs="+",
        default=None,
        help="Argv of the evaluation command, e.g. /workspace/scripts/evaluate.sh.",
    )
    compose.add_argument(
        "--eval-working-dir",
        default=None,
        help="Working directory inside the container for the exec'd command.",
    )
    compose.add_argument(
        "--eval-metrics",
        default=None,
        help=(
            "Comma-separated metric names the evaluator's JSON output promises to emit "
            "(e.g. suite_runtime_ms,correctness). Must include --metric and, if set, "
            "--guardrail-metric-id."
        ),
    )
    compose.add_argument(
        "--eval-repetitions",
        type=int,
        default=None,
        help="Default: language/metric-based default (see _WORKLOAD_DEFAULTS_BY_LANGUAGE).",
    )
    compose.add_argument("--eval-warmup", type=int, default=None, help="Default: language-based.")
    compose.add_argument(
        "--eval-timeout", type=int, default=300, help="Per-exec timeout in seconds."
    )
    compose.add_argument(
        "--application-services",
        default=None,
        help=(
            "Comma-separated Compose services `up --wait` waits on before evaluating "
            "(default: just --eval-service)."
        ),
    )
    args = parser.parse_args(argv)
    if (
        args.command_id
        and args.unit_command
        and args.command_id != args.unit_command
    ):
        parser.error("--command-id and --unit-command disagree -- pass only one")
    args.unit_command = args.unit_command or args.command_id
    if args.execution_profile == "legacy_discovery" and not any(
        (args.build_command, args.lint_command, args.type_command, args.unit_command)
    ):
        parser.error(
            "at least one of --build-command/--lint-command/--type-command/"
            "--unit-command (or the deprecated --command-id) is required for "
            "--execution-profile legacy_discovery"
        )
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
    return args


def _commands_kwarg(args: argparse.Namespace) -> dict[str, Any]:
    commands = {
        kind: value
        for kind, value in (
            ("build", args.build_command),
            ("lint", args.lint_command),
            ("type", args.type_command),
            ("unit", args.unit_command),
        )
        if value
    }
    return {"commands": commands} if commands else {}


def build_payload(args: argparse.Namespace) -> ManualCasePayload:
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
        execution_profile=args.execution_profile,
        guardrail_metric_id=args.guardrail_metric_id,
        maximum_worker_seconds=args.maximum_worker_seconds,
        deadline_seconds=args.deadline_seconds,
        actor_id=args.actor_id,
        actor_role="owner",  # auto-approves at A1.90 -- this CLI has no interrupt-resume flow
        **_commands_kwarg(args),
        **compose_kwargs,
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
    real human-in-the-loop halt as this CLI's own owner (mirrors
    `optimize_and_apply.py`'s copy of this helper) -- the one decision an
    unattended CLI run can make on its own owner's behalf, instead of
    prompting a human. Used for A2.31's LLM-suggested command approval when
    no repository convention (pyproject.toml, etc.) was detected."""

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


def print_a3_finding_rejection_diagnostics(store: Any, state: dict[str, Any]) -> None:
    if state.get("node_routes", {}).get("A3.51") != "rejected":
        return
    draft_ref = ref_by_type(state, "FindingDraftSet")
    citation_ref = ref_by_type(state, "CitationResolutionReportSet")
    judgement_ref = ref_by_type(state, "FindingJudgementSet")
    if draft_ref is None:
        return

    print("A3 finding gate rejected before FindingSet:")
    drafts = read_model(store, _TENANT_ID, draft_ref, FindingDraftSet)
    if drafts.generation_failures:
        for failure in drafts.generation_failures:
            print(f"  - generation failure: {failure}")
    citations = (
        read_model(store, _TENANT_ID, citation_ref, CitationResolutionReportSet)
        if citation_ref is not None
        else None
    )
    judgements = (
        read_model(store, _TENANT_ID, judgement_ref, FindingJudgementSet)
        if judgement_ref is not None
        else None
    )
    citations_by_finding = {r.finding_id: r for r in citations.reports} if citations else {}
    judgement_by_finding = {j.finding_id: j for j in judgements.judgements} if judgements else {}
    for draft in drafts.drafts:
        print(f"  - draft {draft.finding_id}: evidence={draft.supporting_evidence_ids}")
        citation = citations_by_finding.get(draft.finding_id)
        if citation is None:
            print("      citation: missing")
        else:
            print(f"      citation: all_resolved={citation.all_resolved}")
            for entry in citation.entries:
                if not (entry.resolved and entry.in_scope and entry.supports_statement):
                    print(
                        "        "
                        f"{entry.evidence_id}: resolved={entry.resolved} "
                        f"in_scope={entry.in_scope} supports={entry.supports_statement} "
                        f"reason={entry.reason}"
                    )
        judgement = judgement_by_finding.get(draft.finding_id)
        if judgement is None:
            print("      judge: missing or invalid model response")
        else:
            print(f"      judge: {judgement.verdict} ({'; '.join(judgement.reasons)})")
    print()


def main() -> None:
    args = parse_args(sys.argv[1:])
    if args.trace_node_outputs:
        os.environ["OPTIMIZER_TRACE_NODE_OUTPUTS"] = "1"
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
    a1_graph = build_a1_graph(build_a1_runtime(ports=a1_ports))
    state = a1_graph.invoke(
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
        return
    if state.get("request_ref") is None:
        print(f"A1 did not produce a request (routes: {state.get('node_routes')}).")
        return
    print(f"A1 completed: {sorted(state.get('completed_nodes', []))}")
    print()

    print("=== A2: collecting real evidence (pytest/ruff via LocalWorkerBroker) ===")
    model_provider, model_id = select_model_provider(
        tenant_id=_TENANT_ID,
        thread_id=thread_id,
        **model_selection_kwargs_from_args(args),
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
            for requirement_id, satisfied in quality.mandatory_coverage.items():
                if not satisfied:
                    print(f"  - mandatory requirement NOT satisfied: {requirement_id}")
            for failure in quality.sample_failures:
                print(f"  - {failure}")
            if not quality.passed and args.metric not in {
                "unit_command_result",
                "lint_command_result",
            }:
                print(
                    f"  Hint: --metric {args.metric} requires benchmark/telemetry samples that "
                    "today's A2 cannot honestly collect (no benchmark/telemetry collector exists "
                    "yet -- A2.62/A2.63 always report unavailable). Only command-exit-code-based "
                    "metrics (unit_command_result, lint_command_result) are collectible today. "
                    "This is a known Lane 1 limitation, not a bug in this run."
                )
        print(f"completed_nodes: {sorted(state.get('completed_nodes', []))}")
        return

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
        build_a3_runtime(ports=a3_ports),
        checkpointer=CONTROL_PLANE.checkpointer,
    ).invoke(state, config={"configurable": {"thread_id": thread_id}})
    a3_routes = {k: v for k, v in state.get("node_routes", {}).items() if k.startswith("A3")}
    print(f"node_routes (A3): {a3_routes}")
    print()

    signal_ref = ref_by_type(state, "ProblemSignalSet")
    if signal_ref is not None:
        signals = read_model(store, _TENANT_ID, signal_ref, ProblemSignalSet)
        print(f"Problem signals detected: {len(signals.signals)}")
        for signal in signals.signals:
            print(f"  - {signal.signal_id}: {signal.description}")
        print()

    finding_ref = ref_by_type(state, "FindingSet")
    if finding_ref is not None:
        findings = read_model(store, _TENANT_ID, finding_ref, FindingSet)
        print(f"Findings sealed: {len(findings.findings)}")
        for finding in findings.findings:
            print(
                f"  - {finding.finding_id} ({finding.claim_type}, "
                f"trust={finding.trust_level.value}, judge={finding.judgement.verdict}): "
                f"{finding.causal_claim}"
            )
        print()
    else:
        print_a3_finding_rejection_diagnostics(store, state)

    quality_ref = ref_by_type(state, "A3QualityReport")
    quality: A3QualityReport | None = None
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
            print(f"      mechanism: {strategy.mechanism}")
    else:
        print("A3 did not reach a sealed SolutionPortfolio.")
        directive_refs = [
            ref
            for ref in state.get("artifact_refs", [])
            if ref.artifact_type == "RevisionDirective"
        ]
        if directive_refs:
            directives = [
                read_model(store, _TENANT_ID, ref, RevisionDirective) for ref in directive_refs
            ]
            latest = max(directives, key=lambda directive: directive.attempt_number)
            print(f"  (stopped in the revision loop after attempt {latest.attempt_number})")
            print(f"  reason: {latest.reason}")
            if latest.targeted_finding_ids:
                print(f"  targeted_finding_ids: {latest.targeted_finding_ids}")
            if latest.targeted_strategy_ids:
                print(f"  targeted_strategy_ids: {latest.targeted_strategy_ids}")
        return

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


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # `a3_handlers`/`a2_handlers` call `ports.model.complete()` bare, on
        # purpose: a node must not hide a dead provider. `GenericModelProvider`
        # already turns any SDK failure into `ModelProviderError`; this just
        # renders it as a clean one-line explanation + exit code instead of a
        # raw traceback. Anything else is re-raised unchanged.
        raise SystemExit(report_model_provider_error(error)) from None
