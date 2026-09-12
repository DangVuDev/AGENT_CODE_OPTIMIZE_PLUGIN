"""CLI: point Lane 1 at a real codebase and an objective, and it runs.

    python scripts/optimize.py <path-to-repo> --objective "Fix the failing tests"

Runs the real, production A1 -> A2 -> A3 graphs end to end:
  - A1 parses the objective/criteria into a sealed `OptimizationRequest`
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

    python scripts/optimize.py fixtures/sample-repo --objective "Fix the failing tests"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _lane1_common import (
    AllowPolicy,
    MemoryArtifactStore,
    MemoryIntentLedger,
    read_model,
    ref_by_type,
    select_model_provider,
)

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
from production_optimizer.contracts.a1 import ManualCasePayload
from production_optimizer.contracts.a2 import BaselineSnapshot, EvidenceQualityReport
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    FindingSet,
    ProblemSignalSet,
    SolutionPortfolio,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.c0 import ConvergedCase, ConvergenceDecision
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.orchestration.subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_c0_graph,
)

_TENANT_ID = "TENANT-CLI"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Lane 1 (A1 -> A2 -> A3) against a real local repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("repo_path", type=Path, help="Path to the target codebase.")
    parser.add_argument("--objective", required=True, help="What you want optimized (free text).")
    parser.add_argument(
        "--feature-id", default="local-feature", help="Feature identifier (default: local-feature)."
    )
    parser.add_argument(
        "--metric",
        default=None,
        help=(
            "Optional performance metric_id for a criterion (e.g. p95_latency_ms). "
            "Only meaningful if a real benchmark/telemetry collector exists for it -- "
            "today's A2 has none (see A2_BLOCKED_NODES / a2_handlers.py), so a "
            "performance criterion will legitimately never match evidence and "
            "produce no signal. The correctness guardrail (real pytest exit code) "
            "always works regardless."
        ),
    )
    parser.add_argument("--target", type=float, default=100.0, help="Target value for --metric.")
    parser.add_argument("--unit", default="ms", help="Unit for --metric.")
    parser.add_argument(
        "--direction", default="minimize", choices=["minimize", "maximize", "target"]
    )
    parser.add_argument("--case-id", default="OPT-CLI-1")
    parser.add_argument("--actor-id", default="cli-user")
    return parser.parse_args(argv)


def build_payload(args: argparse.Namespace) -> ManualCasePayload:
    structured: dict[str, Any] = {
        "objective": {"statement": args.objective, "feature_id": args.feature_id},
        "workload": {"workload_id": f"{args.feature_id}-workload", "environment_id": "local-dev"},
    }
    if args.metric:
        structured["criteria"] = [
            {
                "metric_id": args.metric,
                "direction": args.direction,
                "target": args.target,
                "unit": args.unit,
            }
        ]
    else:
        # A1.61 requires at least one criterion; a generic placeholder keeps
        # the request valid without claiming a performance target this CLI
        # invocation never asked for.
        structured["criteria"] = [
            {"metric_id": "p95_latency_ms", "direction": "minimize", "target": 100.0, "unit": "ms"}
        ]

    return ManualCasePayload(
        structured_request=structured,
        local_path=str(args.repo_path),
        allowed_root=str(args.repo_path),
        actor_id=args.actor_id,
        actor_role="owner",  # auto-approves at A1.90 -- this CLI has no interrupt-resume flow
    )


def seed_payload(store: MemoryArtifactStore, payload: ManualCasePayload) -> ArtifactRef:
    content = canonical_json(payload)
    ref = ArtifactRef(
        artifact_type="ManualCasePayload",
        schema_version="1.0",
        artifact_id="payload",
        content_digest=sha256_digest(content),
        uri="memory://payload",
    )
    store.seed_json(ref, content)
    return ref


def main() -> None:
    args = parse_args(sys.argv[1:])
    repo_path = args.repo_path.resolve()
    if not repo_path.exists():
        raise SystemExit(f"repo path does not exist: {repo_path}")
    args.repo_path = repo_path

    print(f"Target codebase: {repo_path}")
    print(f"Objective: {args.objective}")
    print()

    store = MemoryArtifactStore()
    payload_ref = seed_payload(store, build_payload(args))

    print("=== A1: parsing intent into a sealed OptimizationRequest ===")
    a1_ports = NodePorts(artifacts=store, intents=MemoryIntentLedger())
    a1_graph = build_a1_graph(build_a1_runtime(ports=a1_ports))
    state = a1_graph.invoke(
        {
            "case_id": args.case_id,
            "thread_id": f"THREAD-{args.case_id}",
            "tenant_id": _TENANT_ID,
            "entrypoint": "manual",
            "lane": "manual",
            "baseline_mode": "active_collection",
            "artifact_refs": [payload_ref],
        }
    )

    pending = state.get("pending_interrupt")
    if pending is not None:
        print(
            f"A1 stopped for approval/clarification at {pending.stage}: {pending.allowed_decisions}"
        )
        print("This CLI has no interrupt-resume flow -- adjust --objective/--metric and rerun.")
        return
    if state.get("request_ref") is None:
        print(f"A1 did not produce a request (routes: {state.get('node_routes')}).")
        return
    print(f"A1 completed: {sorted(state.get('completed_nodes', []))}")
    print()

    print("=== A2: collecting real evidence (pytest/ruff via LocalWorkerBroker) ===")
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
    )
    try:
        a2_ports = NodePorts(
            artifacts=store, intents=MemoryIntentLedger(), policy=AllowPolicy(), workers=broker
        )
        state = build_a2_graph(build_a2_runtime(ports=a2_ports)).invoke(state)
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
            if not quality.passed and args.metric is None:
                print(
                    "  Hint: the default performance criterion (p95_latency_ms) requires 3 "
                    "benchmark/test/telemetry samples that today's A2 cannot honestly collect "
                    "(no benchmark/telemetry collector exists yet -- A2.62/A2.63 always report "
                    "unavailable). Only the correctness guardrail (real pytest exit code) is "
                    "collectible today. This is a known Lane 1 limitation, not a bug in this run."
                )
        print(f"completed_nodes: {sorted(state.get('completed_nodes', []))}")
        return

    baseline = read_model(store, _TENANT_ID, baseline_ref, BaselineSnapshot)
    print("Real metric aggregates collected:")
    for aggregate in baseline.aggregates:
        print(f"  - {aggregate.metric_id}: mean={aggregate.mean} unit={aggregate.unit}")
    print()

    print("=== A3: analyzing evidence and proposing strategies ===")
    model_provider, model_id = select_model_provider(tenant_id=_TENANT_ID)
    a3_ports = NodePorts(
        artifacts=store,
        intents=MemoryIntentLedger(),
        policy=AllowPolicy(),
        model=model_provider,
        model_id=model_id,
    )
    state = build_a3_graph(build_a3_runtime(ports=a3_ports)).invoke(state)
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

    quality_ref = ref_by_type(state, "A3QualityReport")
    if quality_ref is not None:
        quality = read_model(store, _TENANT_ID, quality_ref, A3QualityReport)
        print(f"A3 quality gate passed: {quality.passed}")
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
        directive_ref = ref_by_type(state, "RevisionDirective")
        if directive_ref is not None:
            print("  (stopped in the revision loop -- see RevisionDirective for why)")
        return

    print()
    print("=== C0: converging the case ===")
    c0_ports = NodePorts(artifacts=store, intents=MemoryIntentLedger(), policy=AllowPolicy())
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
    main()
