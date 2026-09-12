# 10. Current Implementation Gap Analysis

Assessment date: 2026-09-12. This assessment is based on the files present in
this workspace and on real end-to-end execution (`scripts/optimize.py` against
`fixtures/sample-repo` and real GitHub clones), not on roadmap statements or an
earlier repository state.

## Conclusion

The repository now contains a production blueprint, a fail-closed
orchestration frame, and **real, tested production handlers for all 99
business task IDs** across both lanes and the shared convergence gate (A1, A2,
A3, B1, B2, C0 — see `../implementation/04-orchestration-frame-as-built.md`).
Lane A is a working, end-to-end-runnable optimizer: `scripts/optimize.py
<repo> --objective "..."` genuinely parses the objective, runs the target
repository's own pytest/ruff/CI commands via `LocalWorkerBroker`, calls a real
model provider, and converges through C0 to a sealed `ConvergedCase`. Lane B's
handlers are equally real (B1 discovery through B2 proposal, both mounting the
same real A2/A3 subgraphs Lane A uses) but have only been exercised through
contract tests that seed state directly — Lane B has no scheduler/CLI trigger
of its own yet.

What remains not implemented is narrower and more specific than "business
handlers": B1.32-35's real metrics/logs/traces/LLM-evidence query adapters (no
port wired in — these nodes always report an honest `unavailable_reason`), a
Lane B trigger surface, an OPA policy adapter (only the deterministic Python
one is wired), live acceptance evidence for the implemented-but-unwired
`KubernetesWorkerBroker`, differentiated C0 failure routing (today: a single
`rejected` outcome with reasons recorded in the artifact, not per-cause graph
edges), and any public API/MCP surface or production deployment automation.

Roadmap and design documents describe target behavior; the as-built documents
(`../implementation/01-project-scaffold.md`,
`../implementation/04-orchestration-frame-as-built.md`) carry the current
evidence.

## Documentation Coverage

| Area | Present | Remaining documentation gate |
|---|---|---|
| Product and requirements | Canonical requirements, decisions, scope profiles and traceability | Change-control owner names and organization-specific policy values before production |
| End-to-end topology | Root graph, lane, shared flow and rollout topology | Executable graph manifest must later be generated from code and compared in CI |
| Lane A business rules | Complete A1.10-A3.90 task catalogues, gates and failure routes | None before implementation; organization-specific thresholds remain configuration |
| Lane 1 graph design | Exhaustive 54-node topology, state/reducers, node runtime, interrupts and framework ownership | Exact library/image versions require ADR and lockfile during implementation |
| Two-lane product design | All 99 A1-A3/B1-B2/C0 task definitions, scan/case isolation, A2/A3 reuse, framework ownership and ordered delivery waves | Organization policies, pilot repository and adapter ADR decisions before implementation |
| Canonical artifacts | Vocabulary, identity/version convention, evidence and treatment model | Field-level Pydantic models, schemas, fixtures and compatibility policy remain code deliverables |
| Production operations | Stage ownership roles, idempotency, SLO, retry/error and runbook outcomes | Named accountable teams, actual runbook URLs and calibrated SLOs before release |
| Security/readiness | Isolation, secrets, approval, observability and release gates | Threat model and deployment-specific controls before live adapters |

## Implementation Coverage

| Capability | As-built status | Required implementation evidence |
|---|---|---|
| Root LangGraph scaffold | Manual/discovery/qualified-case routing implemented; PostgreSQL checkpointer provider implemented, live restart evidence still environment-gated | FRAME-0 restart, interrupt, idempotency and composition tests against a reachable Postgres |
| Lane A LangGraph | A1/A2/A3/C0 topology, fan-outs and routes compiled; all handlers real and registered | Broader real-repository corpus (beyond `fixtures/sample-repo`, `fixtures/urlshortener-sample` and ad hoc GitHub clones already exercised this session) |
| Lane B/C0 LangGraph | Native B1/B2/C0 topology compiled and real; B1.95 reuses real A2, B2.22 reuses real A3 | Candidate-level `Send`, scan/case outbox, durable replay, a real trigger surface, and real-repository corpus tests (today: contract tests with seeded state) |
| A1 | Implemented (`application/a1_handlers.py`) | Real end-to-end evidence exists (`tests/contract/test_a1_production_handlers.py`, `scripts/optimize.py` runs) |
| A2 | Implemented (`application/a2_handlers.py`, `a2_worker_capabilities.py`) | Real command execution (git/pytest/ruff/`act`-CI/pytest-benchmark) via `LocalWorkerBroker`; 3-tier command detection with mandatory human approval for LLM-suggested commands |
| A3 | Implemented (`application/a3_handlers.py`) | Real model-provider calls (5 providers), citation resolution, bounded revision loop, sealed `FindingSet`/`SolutionPortfolio`/`A3QualityReport` |
| B1 | Implemented (`application/b1_handlers.py`, 25 native nodes) | Real scan/fingerprint/git-history/policy-authorization and detector/scoring/qualification logic; B1.32-35 honestly report `unavailable_reason` (no query adapter yet) |
| B2 | Implemented (`application/b2_handlers.py`, 11 native nodes) | Real analysis-strategy/staleness/risk-routing logic and `ProposalEnvelope` sealing; no scheduler/CLI trigger yet |
| C0 | Implemented (`application/c0_handlers.py`, 7 nodes, shared by both lanes) | Real schema/digest-chain/semantic-equivalence/freshness checks and a computed (not hardcoded) convergence gate; verified against a real Lane A run |
| Persistence | Strict pooled PostgreSQL checkpointer provider plus case/intent/outbox adapters, migrations and restart-recovery integration tests | HA and disaster-recovery drills against managed infrastructure |
| Artifact storage | `S3ArtifactStore` implemented: content-addressed, create-only writes, digest verification, tenant isolation | Encryption-at-rest/KMS, retention policy and reconciliation tests against a real S3/MinIO fleet |
| Policy and identity | `DeterministicPythonPolicy` (fail-closed registry) and `JwtIdentityPort` implemented with golden-decision tests | An OPA HTTP adapter implementing the same `PolicyPort`; the deterministic Python policy remains the primary production mechanism by project decision |
| Worker isolation | `LocalWorkerBroker` (in-process, timeout-enforced) implemented and used by both lanes; `KubernetesWorkerBroker` implemented and unit-tested but unwired and never run against a live cluster | Live Kubernetes/gVisor sandbox acceptance evidence; `LocalWorkerBroker` does not yet cancel/kill a timed-out subprocess |
| Collector/analyzer/model adapters | Command-execution collection (git/pytest/ruff/`act`/pytest-benchmark) and 5 model-provider adapters (Anthropic/OpenAI/Gemini/DeepSeek/Ollama) implemented | A real performance/benchmark metric collector for arbitrary criteria beyond command-exit-code signals; B1's historical metrics/logs/traces/LLM-evidence query adapters |
| Public API/CLI/MCP | `scripts/optimize.py` is a real Lane A CLI | No API/MCP surface; no Lane B CLI/scheduler; no create/read/stream/resume/cancel service bound to a durable thread |
| Operations | OpenTelemetry span emission implemented; no raw payloads in telemetry | Dashboards, alerts, runbooks, backup/restore, load/chaos/security drills |

## Highest-Risk Architecture Gap (Resolved)

The original risk was encoding Lane 1 as a flat sequence:

```text
a1 -> a2 -> execute_a1_a2_a3 -> END
```

or as three nodes that call synchronous stage services. The implementation
instead uses, and `tests/contract/test_root_graph.py` verifies, the nested
shape this section originally required:

```text
OptimizationRootGraph
  -> LaneASubgraph
       -> A1RequirementSubgraph: every A1.10-A1.95 task node
       -> A2BaselineSubgraph: every A2.10-A2.95 task node and collector fan-out
       -> A3SolutionSubgraph: every A3.10-A3.90 task node, analyzer fan-out,
          deterministic gates and bounded targeted revision
  -> C0: shared convergence, mounted after both lanes
```

The exhaustive contract is
[`../implementation/05-lane-1-detailed-implementation-playbook.md`](../implementation/05-lane-1-detailed-implementation-playbook.md).
`tests/contract/test_root_graph.py` compares the compiled graph inventory to
the catalog so future refactoring cannot silently collapse checkpoints or
bypass gates.

## Implementation Order

1. Establish the canonical project-owned repository, ownership/governance,
   pinned developer toolchain, lock strategy, CI and security scanning.
2. Stand up the local platform composition and define production infrastructure
   ports, migrations and conformance tests for checkpoint, case/idempotency,
   artifact, policy, identity, worker, secrets and telemetry boundaries.
3. Create the root LangGraph frame, compact state/reducers, universal node
   runtime and restart/interrupt/idempotency smoke tests without Lane 1 business
   handlers.
4. Register the compiled two-lane topology and topology tests for all 99
   business task IDs. **Done** — structural step and topology tests both in
   place.
5. Implement canonical envelope, interrupt, error, event, artifact reference,
   digest and schema contracts, then A1 handlers and approval/clarification
   resume paths. **Done.**
6. Implement A2 snapshot, worker fan-out, raw preservation, provenance,
   quality and comparability handlers. **Done**, including real command
   execution and 3-tier command detection with mandatory human approval.
7. Implement A3 analyzer/generator fan-out, citation/judge/maturity gates,
   strategy validation and targeted revision handlers. **Done**, including
   real model-provider calls across 5 providers.
7a. Implement B1 discovery, B2 proposal and the shared C0 convergence gate.
    **Done** — not originally numbered in this order, but completed alongside
    7 above; B1.32-35's query adapters and a Lane B trigger surface remain
    open (see Implementation Coverage).
8. Add development adapters only under an explicit development profile; never
   use their presence as a production-readiness claim.
9. Add PostgreSQL, object storage, policy/identity, isolated worker and live
   provider adapters with conformance tests. **Mostly done** — Postgres, S3,
   JWT identity, deterministic Python policy and `LocalWorkerBroker` all have
   production adapters and tests; `KubernetesWorkerBroker` and an OPA policy
   adapter remain.
10. Add API/CLI/MCP surfaces and operational telemetry. **Partially done** —
    `scripts/optimize.py` is a real Lane A CLI and OpenTelemetry spans are
    emitted; no API/MCP surface and no Lane B CLI/scheduler exist yet.
11. Run schema, topology, contract, integration, resume, chaos, security and
    real-case reconstruction release gates. **Partially done** — schema,
    topology, contract and resume evidence exist; chaos/security drills and a
    broader real-case corpus remain.

The normative pre-node order and clone/install/pull rules are in
[`../implementation/00-bootstrap-and-build-order.md`](../implementation/00-bootstrap-and-build-order.md).

## Permitted Release Claim

At the current workspace state, the repository may accurately be described as:

> A working Lane A optimizer — real production handlers for A1, A2, A3 and the
> shared C0 convergence gate, runnable end to end against a real repository via
> `scripts/optimize.py` — plus a Lane B (B1 discovery, B2 proposal) that is
> equally real in its handler logic but has no scheduler/CLI trigger of its own
> and has only been exercised through contract tests with seeded state.

It must not be called fully autonomous or production-ready: B1.32-35's real
query adapters, a Lane B trigger surface, an OPA policy adapter,
`KubernetesWorkerBroker` acceptance evidence, differentiated C0 failure
routing, and API/MCP/operational surfaces are all still open. It must not be
called validated against a broad real-world corpus beyond the specific repos
(`fixtures/sample-repo`, `fixtures/urlshortener-sample`, and ad hoc GitHub
clones) exercised so far. It must not be called production-ready until the
M0/M1 infrastructure and operational gates also pass.
