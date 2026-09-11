# 10. Current Implementation Gap Analysis

Assessment date: 2026-09-11. This assessment is based on the files present in
this workspace, not on roadmap statements or an earlier repository state.

## Conclusion

The repository currently contains a production blueprint, complete two-lane
implementation design through C0, and a fail-closed orchestration frame. The frame includes packaging,
locked dependencies, compact contracts, infrastructure ports, all three root
entrypoints, the 99-task A1/A2/A3/B1/B2/C0 topology, development infrastructure
configuration, migration skeleton and contract tests. It does not implement
business handlers or any
collector, analyzer, model, storage, policy, identity, worker, public API/CLI/MCP
or production deployment adapter.

Roadmap and design documents describe target behavior. Only items listed in
[`../implementation/01-project-scaffold.md`](../implementation/01-project-scaffold.md)
have current as-built evidence.

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
| Root LangGraph scaffold | Manual/discovery/qualified-case routing implemented; no durable checkpointer | FRAME-0 restart, interrupt, idempotency and composition tests |
| Lane A LangGraph | A1/A2/A3/C0 topology, fan-outs and fail-closed routes compiled; zero handlers enabled | Handler contracts, durable resume and real-artifact end-to-end evidence |
| Lane B/C0 LangGraph | Native B1/B2/C0 topology compiled; B1.95 reuses A2 and B2.22 reuses A3 | Candidate-level `Send`, scan/case outbox, durable replay and real-artifact tests |
| A1 | Not implemented | Typed contracts, extraction, path/Git resolution, project/scope discovery, deterministic gates, RBAC/approval interrupt and immutable artifacts |
| A2 | Not implemented | Content-addressed snapshot, manifest, safe worker adapters, raw evidence store, provenance, quality/comparability gates and interrupts |
| A3 | Not implemented | Evidence catalogue, analyzers, generator/judge separation, citations, maturity/risk/portfolio policy, bounded revision and sealed artifacts |
| Persistence | Strict pooled PostgreSQL checkpointer provider plus case/intent/outbox ports and initial SQL; no live integration evidence | Case store, idempotency ledger, migrations, HA and recovery tests |
| Artifact storage | Port only; no adapter | Encrypted immutable object storage, digest/signature verification, tenancy, retention and reconciliation tests |
| Policy and identity | Ports plus fail-closed bootstrap Rego only; no adapter | Authenticated identity/RBAC and versioned policy adapter with golden tests |
| Worker isolation | Port only; no worker | Leases, queue/callback protocol, Kubernetes/gVisor or approved equivalent, timeout/cancel/reconcile and security tests |
| Collector/analyzer/model adapters | Not implemented | Exact version ADRs, typed ports, conformance suites, provenance, budgets and failure behavior |
| Public API/CLI/MCP | Not implemented | Create/read/stream/resume/cancel operations bound to one durable thread and authorization tests |
| Operations | Not implemented | Telemetry, dashboards, alerts, runbooks, backup/restore, load/chaos/security drills |

## Highest-Risk Architecture Gap

The first implementation must not encode Lane 1 as:

```text
a1 -> a2 -> execute_a1_a2_a3 -> END
```

or as three nodes that call synchronous stage services. The required shape is:

```text
OptimizationRootGraph
  -> LaneASubgraph
       -> A1RequirementSubgraph: every A1.10-A1.95 task node
       -> A2BaselineSubgraph: every A2.10-A2.95 task node and collector fan-out
       -> A3SolutionSubgraph: every A3.10-A3.90 task node, analyzer fan-out,
          deterministic gates and bounded targeted revision
  -> C0 (later milestone)
```

The exhaustive contract is
[`../implementation/lane-1-langgraph-architecture.md`](../implementation/lane-1-langgraph-architecture.md).
CI must compare the compiled graph inventory to that contract so future
refactoring cannot silently collapse checkpoints or bypass gates.

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
   business task IDs. This structural step is implemented; handlers remain
   disabled.
5. Implement canonical envelope, interrupt, error, event, artifact reference,
   digest and schema contracts, then A1 handlers and approval/clarification
   resume paths.
6. Implement A2 snapshot, worker fan-out, raw preservation, provenance,
   quality and comparability handlers.
7. Implement A3 analyzer/generator fan-out, citation/judge/maturity gates,
   strategy validation and targeted revision handlers.
8. Add development adapters only under an explicit development profile; never
   use their presence as a production-readiness claim.
9. Add PostgreSQL, object storage, policy/identity, isolated worker and live
   provider adapters with conformance tests.
10. Add API/CLI/MCP surfaces and operational telemetry.
11. Run schema, topology, contract, integration, resume, chaos, security and
    real-case reconstruction release gates.

The normative pre-node order and clone/install/pull rules are in
[`../implementation/00-bootstrap-and-build-order.md`](../implementation/00-bootstrap-and-build-order.md).

## Permitted Release Claim

At the current workspace state, the repository may accurately be described as:

> Production blueprint with a tested, fail-closed two-lane LangGraph
> orchestration frame and no enabled business handlers.

It must not be called an implemented Lane A or working optimization workflow
until A1-A3 code and test evidence exist. It must not be called production-ready
until the M0/M1 infrastructure and operational gates also pass.
