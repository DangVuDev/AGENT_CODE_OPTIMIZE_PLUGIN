"""End-to-end Lane 1 demo: a real A2 -> A3 run against `fixtures/sample-repo`.

`fixtures/sample-repo` is a small but real Python package with a genuine
reproducible bug (one deliberately wrong test assertion), a real lint
violation (unused import), a real long function and a real bare `except` --
so every artifact this script prints is computed from that repository, not
fabricated for the demo.

A2 always runs for real: `LocalWorkerBroker` genuinely executes pytest/ruff
against the fixture via `application.a2_worker_capabilities`.

A3.40/A3.50/A3.60 need a `ModelProviderPort`. This script uses a real one if
it finds credentials for it (checked in order: Anthropic, OpenAI, Gemini,
DeepSeek, then a locally reachable Ollama server) and falls back to a
deterministic *local stand-in* otherwise -- clearly labeled as such, and
still grounded in this run's real evidence IDs (it does not invent data),
but not a real model. Set one of the API key environment variables (see
`.env.example`) to see genuine LLM output instead.

This script intentionally constructs `OptimizationRequest` directly rather
than running the full A1 subgraph, so it always produces the exact same,
easy-to-read fixture output regardless of A1's own parsing heuristics.
**To run Lane 1 against your own codebase with a real A1 pass, use
`scripts/optimize.py` instead** -- this script is a fixed, repeatable demo
of A2+A3 specifically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from _lane1_common import (
    AllowPolicy,
    MemoryArtifactStore,
    MemoryIntentLedger,
    read_model,
    ref_by_type,
    select_model_provider,
)

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import NodePorts, build_a2_runtime, build_a3_runtime
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Guardrail,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.a2 import BaselineSnapshot
from production_optimizer.contracts.a3 import (
    A3QualityReport,
    FindingSet,
    ProblemSignalSet,
    SolutionPortfolio,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ProducerIdentity
from production_optimizer.orchestration.subgraphs import build_a2_graph, build_a3_graph

ROOT_DIR = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT_DIR / "fixtures" / "sample-repo"
_TENANT_ID = "TENANT-DEMO"
_CASE_ID = "OPT-DEMO-LANE1"
_ZERO_DIGEST = "sha256:" + "0" * 64
_PRODUCER = ProducerIdentity(name="demo-lane1", version="1.0.0")
_POLICY_VERSION = "intake-policy-v1"


def build_request() -> OptimizationRequest:
    request = OptimizationRequest(
        artifact_id="request-1",
        tenant_id=_TENANT_ID,
        case_id=_CASE_ID,
        created_at=datetime.now(UTC),
        producer=_PRODUCER,
        origin=Origin.MANUAL,
        scope_profile=ScopeProfile.LOCAL_SANDBOX,
        source=SourceReference(
            repository_id="inventory-sample",
            allowed_root_id=str(FIXTURE_ROOT.parent),
            relative_path=FIXTURE_ROOT.name,
            requested_revision=None,
        ),
        objective=Objective(
            statement="Fix the failing pricing test and clean up the lint violation",
            feature_id="pricing",
        ),
        criteria=[
            Criterion(
                criterion_id="primary",
                metric_id="p95_latency_ms",
                direction="minimize",
                target=100.0,
                unit="ms",
                weight=1.0,
            )
        ],
        guardrails=[
            Guardrail(
                guardrail_id="correctness",
                metric_id="unit_command_result",
                operator="eq",
                threshold=0.0,
                unit="exit_code",
            )
        ],
        workload=WorkloadContract(
            workload_id="inventory-workload",
            environment_id="local-dev",
            repetitions=1,
            warmup_runs=0,
            concurrency=1,
            cache_state="warm",
        ),
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="evidence-primary",
                criterion_id="primary",
                accepted_source_types={"test"},
                minimum_samples=1,
                mandatory=True,
            )
        ],
        budget=ExecutionBudget(
            deadline_seconds=300,
            maximum_worker_seconds=180,
            maximum_model_tokens=200_000,
            maximum_storage_bytes=50_000_000,
        ),
        approval=ApprovalBinding(
            approval_id="approval-1",
            actor_id="owner-1",
            actor_role="owner",
            decision="approve",
            artifact_digest=_ZERO_DIGEST,
            policy_version=_POLICY_VERSION,
        ),
        request_fingerprint=_ZERO_DIGEST,
        content_digest=_ZERO_DIGEST,
    )
    return request.model_copy(update={"content_digest": model_content_digest(request)})


def seed_request(store: MemoryArtifactStore, request: OptimizationRequest) -> ArtifactRef:
    content = canonical_json(request)
    ref = ArtifactRef(
        artifact_type="OptimizationRequest",
        schema_version="1.0",
        artifact_id="request-1",
        content_digest=sha256_digest(content),
        uri="memory://request-1",
    )
    store.seed_json(ref, content)
    return ref


def main() -> None:
    if not FIXTURE_ROOT.exists():
        raise SystemExit(f"fixture repository not found at {FIXTURE_ROOT}")

    print(f"Target codebase: {FIXTURE_ROOT}")
    print()

    store = MemoryArtifactStore()
    request = build_request()
    request_ref = seed_request(store, request)

    print("=== A2: collecting real evidence (pytest/ruff via LocalWorkerBroker) ===")
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
    )
    try:
        a2_ports = NodePorts(
            artifacts=store, intents=MemoryIntentLedger(), policy=AllowPolicy(), workers=broker
        )
        a2_graph = build_a2_graph(build_a2_runtime(ports=a2_ports))
        a2_state = a2_graph.invoke(
            {
                "case_id": _CASE_ID,
                "thread_id": f"THREAD-{_CASE_ID}",
                "tenant_id": _TENANT_ID,
                "entrypoint": "manual",
                "lane": "manual",
                "baseline_mode": "active_collection",
                "request_ref": request_ref,
                "artifact_refs": [request_ref],
            }
        )
    finally:
        broker.close()

    baseline_ref = a2_state.get("baseline_ref") or ref_by_type(a2_state, "BaselineSnapshot")
    if baseline_ref is None:
        print("A2 did not produce a BaselineSnapshot -- stopping here.")
        print(f"completed_nodes: {sorted(a2_state.get('completed_nodes', []))}")
        return

    baseline = read_model(store, _TENANT_ID, baseline_ref, BaselineSnapshot)
    print(f"completed_nodes: {sorted(a2_state.get('completed_nodes', []))}")
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
    a3_graph = build_a3_graph(build_a3_runtime(ports=a3_ports))
    a3_state = a3_graph.invoke(a2_state)

    print(f"completed_nodes through A3: {sorted(a3_state.get('completed_nodes', []))}")
    print(f"node_routes: {a3_state.get('node_routes', {})}")
    print()

    signal_ref = ref_by_type(a3_state, "ProblemSignalSet")
    if signal_ref is not None:
        signals = read_model(store, _TENANT_ID, signal_ref, ProblemSignalSet)
        print(f"Problem signals detected: {len(signals.signals)}")
        for signal in signals.signals:
            print(f"  - {signal.signal_id}: {signal.description}")
        print()

    finding_ref = ref_by_type(a3_state, "FindingSet")
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

    quality_ref = ref_by_type(a3_state, "A3QualityReport")
    if quality_ref is not None:
        quality = read_model(store, _TENANT_ID, quality_ref, A3QualityReport)
        print(f"A3 quality gate passed: {quality.passed}")
        print()

    portfolio_ref = a3_state.get("solution_portfolio_ref") or ref_by_type(
        a3_state, "SolutionPortfolio"
    )
    if portfolio_ref is not None:
        portfolio = read_model(store, _TENANT_ID, portfolio_ref, SolutionPortfolio)
        print(f"Solution strategies: {len(portfolio.strategies)}")
        for strategy in portfolio.strategies:
            marker = "ELIGIBLE" if strategy.eligible else "ineligible"
            print(f"  - [{marker}] {strategy.strategy_id}: {strategy.title}")
            print(f"      mechanism: {strategy.mechanism}")
    else:
        print("A3 did not reach a sealed SolutionPortfolio.")
        pending_directive = ref_by_type(a3_state, "RevisionDirective")
        if pending_directive is not None:
            print("  (stopped in the revision loop -- see RevisionDirective for why)")


if __name__ == "__main__":
    main()
