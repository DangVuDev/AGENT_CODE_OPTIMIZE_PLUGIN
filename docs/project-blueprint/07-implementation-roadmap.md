# 07. Implementation Roadmap

## Delivery Principles

- Deliver a vertical slice on a real repository before expanding language scope.
- Every milestone includes artifacts and failure paths, not only a happy path.
- Establish the LangGraph topology and state contracts early, then replace
  adapters incrementally.
- Do not deploy steps 03-08 until A1-A3 and comparability meet their gates.
- Bootstrap from the outside inward: project-owned repository and toolchain,
  platform dependencies and production ports, root LangGraph/node runtime,
  exhaustive empty topology, then business-node handlers. The normative order
  and `BOOT-*`/`FRAME-*` gates are in
  [`../implementation/00-bootstrap-and-build-order.md`](../implementation/00-bootstrap-and-build-order.md).
- The complete two-lane implementation order, shared A2/A3 reuse and C0 product
  gates are normative in
  [`../implementation/03-two-lane-delivery-roadmap.md`](../implementation/03-two-lane-delivery-roadmap.md).

## M0 - Foundation and Contracts

Deliverables:

- Capability-based module boundaries.
- Pydantic and JSON Schema contracts for the complete artifact chain.
- Canonical artifact vocabulary and `SolutionStrategy -> ExperimentPhase ->
  Treatment` models from `06-data-contracts.md`.
- `REQ-*` to node, artifact and release-test traceability generated in CI.
- Root LangGraph plus Lane A, Lane B, shared, phase-loop, and rollout subgraphs.
- Exhaustive graph manifests: every business task ID is a distinct LangGraph
  node; stage IDs are compiled subgraphs and never synchronous stage services.
- PostgreSQL checkpointer, object storage, and case database.
- Event and audit envelopes, idempotency, and error taxonomy.

Exit criteria:

- Graph topology tests pass.
- Lane A topology contains all 54 A1.10-A3.90 nodes and contains no coarse
  `execute_a1_a2_a3`, `execute_everything`, or equivalent bypass node.
- Crash and resume do not duplicate side effects.
- Schema compatibility and artifact-digest tests pass.

## M1 - Production Lane A (A1-A3)

Deliverables:

- Generic A1 intake, registry, RBAC, and clarification/approval interrupts.
- A2 integrations for Loki, Prometheus, OpenTelemetry, and repositories with
  immutable raw evidence.
- Comparability validator.
- A3 Tree-sitter and analyzer adapters, LLM generator and judge, and quality
  gates.
- API, UI, and MCP surfaces exposing node status and evidence.

Exit criteria:

- One real case reconstructs its A1 request, A2 baseline, and A3 solutions.
- No solution is eligible when supported only by negative evidence.
- Collector failure cannot trigger an LLM call.
- Resume succeeds from every interrupt.

## M2 - Production Lane B (B1-B2)

Deliverables:

- Scheduled discovery graph.
- Detection policy, deduplication, cooldown, and owner routing.
- Historical baseline through the same compiled A2 subgraph.
- B2 invocation of the same compiled A3 subgraph and quality gate.
- Per-opportunity B2 case/thread creation from the B1.96 transactional outbox.

Exit criteria:

- Historical telemetry backtests have reviewed precision and recall.
- The cooldown window cannot create duplicate cases.
- Outbox redelivery resolves to the same case/thread and one scan can safely
  produce multiple isolated cases.
- Missing owners or evidence fail closed.

## M3 - Convergence, Selection, and Planning (C0-02)

Deliverables:

- Signed convergence gate.
- OPA-backed ranking and approval policy.
- Plan draft, critic, and deterministic-validator subgraph.
- File, symbol, and command discovery at the pinned commit.

Exit criteria:

- Lane A and Lane B produce the same downstream contract.
- Every plan covers every A1 criterion.
- Every phase contains one treatment plus rollback, tests, and measurement.

## M4 - Isolated Implementation and Verification (03-04)

Deliverables:

- Execution broker, queue, and worker callback nodes.
- Kubernetes Job and gVisor sandbox profiles.
- OpenHands default adapter and optional Claude or Codex adapters.
- Scope and diff validator.
- Repository-native build, test, static-analysis, and security runners.

Exit criteria:

- Agents cannot read secrets, modify files, or use network destinations outside
  policy.
- Cancellation, timeout, and worker failure clean up and resume correctly.
- Out-of-scope patches are rejected.
- A test failure returns only the active phase to implementation.

## M5 - Measurement and Decision (05-06)

Deliverables:

- Reproducible benchmark runner.
- Warmup, repetition, noise, outlier, and statistical policies.
- Before-and-after comparability.
- Deterministic KEEP, FIX_ONE_PART, and REVERT rules.
- Executed and verified rollback.

Exit criteria:

- A/A noise characteristics are recorded.
- Different features, workloads, or commits produce INCOMPARABLE.
- A failed guardrail always reverts or interrupts according to policy.

## M6 - Reporting and Rollout (07-08)

Deliverables:

- Signed JSON and Markdown reports.
- Pull-request integration.
- Argo Rollouts and OpenFeature adapters.
- Canary metrics, observation windows, kill switch, and automatic rollback.

Exit criteria:

- Decisions can be reconstructed from the artifact chain.
- Canary guardrail failure rolls back within the SLO.
- Full rollout remains observed and assigned to an owner.

## M7 - Hardening and Scale

Deliverables:

- Multi-tenant isolation, quotas, and scheduling fairness.
- Highly available workers and checkpointers, backup and restore, and DR drills.
- Cost budgets, model fallback, and circuit breakers.
- SLO dashboards, incident runbooks, retention, and deletion workflows.
- Framework-adapter conformance suites.
- Completed technology ADRs containing pinned versions/digests, license review,
  SBOM/CVE process, data residency, TCO and removal plans.

Exit criteria:

- Load, chaos, security, and disaster-recovery gates pass.
- Normal operation requires no manual database or artifact repair.

## Recommended Adapter Order

1. Existing Loki and Prometheus plus repository indexing.
2. Tree-sitter plus language-native test and build discovery.
3. Pyroscope proof of concept on one service.
4. OpenHands inside Kubernetes and gVisor.
5. Optuna and DSPy for suitable use cases.
6. Slither and Foundry for Solidity.
7. Argo Rollouts and OpenFeature.

## Definition of Done for Every Node

- Typed input and output with a schema version.
- A place in LangGraph with explicit conditional edges.
- Idempotency, timeout, retry, and cancellation behavior.
- Metrics, logs, traces, and artifact references.
- Appropriate unit, contract, integration, failure, and resume tests.
- Security review for data and tool boundaries.
- An owner and runbook.
- Defined SLO and release gate.
