# 04. Orchestration Frame — As Built

Assessment date: 2026-09-11  
Authority: `02-two-lane-product-architecture.md` and
`03-two-lane-delivery-roadmap.md`

## Implemented Boundary

The code now implements the structural LangGraph flow for both product lanes:

```text
OptimizationRootGraph
|-- manual     -> A1 -> shared A2 -> shared A3 -> C0
|-- discovery  -> B1, with shared A2 mounted at B1.95
`-- qualified  -> B2, with shared A3 mounted at B2.22 -> C0
```

The expanded graph inventory contains every one of the 99 documented business
task IDs. A2 collector, A3 analyzer, B1 query, and B1 detector branches use
deterministic fan-in edges. Quality, approval, clarification, comparability,
suppression, refresh, rejection, and targeted-revision routes are represented
as conditional graph edges.

This increment implements orchestration, a deterministic pilot runtime and the
first production-scoped A1 manual-intake handler slice. It still does not claim
production analyzer/collector/model behavior. `NodeRuntime` has no default
handler. A production invocation without an explicit, Definition-of-Ready
registration raises `NodeNotEnabledError` at the owning node. The separate
`build_pilot_runtime()` registration is local validation code: it drives all
documented nodes through LangGraph, emits strict compact artifact references and
keeps source, workers, external telemetry, and model providers untouched.

## Runtime Safety

- Root identity requires case, thread, tenant, lane, and entrypoint.
- Manual entry requires the manual lane; discovery and qualified-case entries
  require the automatic lane.
- Manual entry selects A2 `active_collection`; automatic entries select
  `historical_recovery`.
- Handlers cannot mutate case, thread, tenant, lane, or entrypoint identity.
- Handler updates must target declared compact state fields.
- Returned routes must be declared by the handler's `NodeSpec`.
- A1 production handlers read `ManualCasePayload` through the artifact port,
  create typed intermediate artifacts and freeze `OptimizationRequest@1.0`
  without storing raw request text in graph state.
- The pilot runtime registers all 99 canonical nodes with deterministic
  in-process handlers and strict route declarations.
- Parallel node completion and per-node routes use deterministic reducers that
  reject conflicting replay values.
- Nested-stage completion is checked by its sealing node before the parent can
  continue.

## P1 / FRAME-0 Platform Implementation

The control-plane implementation now includes:

- pooled PostgreSQL LangGraph checkpoints with strict serialization and schema setup;
- PostgreSQL case, node-intent and transactional-outbox repositories with migrations;
- content-addressed S3/MinIO storage with create-only writes and digest verification;
- JWT identity/RBAC and version-bound deterministic Python policy adapters;
- local worker protocol execution and a production Kubernetes Job broker with a
  non-root, read-only, no-service-token security baseline, timeout, cancellation,
  idempotent submission and result reconciliation;
- OpenTelemetry spans/metrics without raw artifact payloads;
- fail-closed, actor/tenant/thread/digest/policy-bound resume authorization; and
- restart/idempotency integration scenarios for the persistent intent ledger.

The PostgreSQL and MinIO integration suites are environment-gated. They are
implemented but cannot be counted as executed evidence unless the development
Compose services are reachable. HA/failover, backup restore, object lock/KMS and
cluster NetworkPolicy/runtime-class enforcement remain deployment acceptance
gates, not properties that unit tests can simulate.

## P2 Contract and Manifest Implementation

- Strict Pydantic contracts cover the A1, A2, A3, B1, B2 and C0 artifact chain,
  platform commands/events/errors/interrupts, registry records and bounded context.
- Canonical JSON and SHA-256 digest generation are deterministic and tested.
- The schema registry generates version-controlled JSON Schema files in `schemas/`;
  CI-style `--check` mode detects drift.
- Repository, feature, owner, metric, workload, query, collector, analyzer, policy
  and model-provider registrations share a tenant-scoped, time-bound version model.
- `manifests/node-manifest.json` contains all 99 unique task IDs. Every entry remains
  disabled and has no handler until its P3+ Definition of Ready is proven.

## Implemented Files

| Area | Files |
|---|---|
| Commands | `contracts/commands.py` |
| Compact state/reducers | `contracts/state.py` |
| Handler boundary | `application/node_runtime.py` |
| A1 production intake | `application/a1_handlers.py`, `tests/contract/test_a1_production_handlers.py` |
| Pilot flow handlers | `application/pilot_handlers.py`, `tests/contract/test_pilot_flow.py` |
| Durable checkpoint provider | `adapters/production/postgres_checkpoint.py` |
| Control-plane adapters | `adapters/production/{postgres_*,s3_artifact_store,jwt_identity,python_policy,otel_telemetry,*worker_broker}.py` |
| Canonical contracts | `contracts/{a1,a2,a3,b1,b2,c0,registries}.py` |
| Schema registry/generator | `contracts/schema_registry.py`, `scripts/generate_contract_assets.py`, `schemas/` |
| Registry resolution | `application/registry.py` |
| Resume authorization | `application/resume.py` |
| Node manifest | `orchestration/manifest.py`, `manifests/node-manifest.json` |
| Canonical inventory | `orchestration/catalog.py` |
| Business graphs | `orchestration/subgraphs/{a1,a2,a3,b1,b2,c0}.py` |
| Lane composition | `orchestration/lanes.py` |
| Root composition | `orchestration/root_graph.py` |
| Contract evidence | `tests/contract/test_root_graph.py`, `tests/unit/test_commands.py` |

## Deliberately Not Claimed

The following remain mandatory roadmap work and are not simulated:

1. Production HA/failover, restore, KMS/object-lock and Kubernetes sandbox/network
   acceptance evidence; these require the target infrastructure.
2. A production secret-manager adapter; only secret-reference contracts exist.
3. Production handlers for A2/A3/B1/B2/C0, analyzer/collector/model
   implementations, and real policy decisions beyond the A1 intake slice and
   deterministic pilot flow.
4. B1 multi-candidate `Send` fan-out and atomic per-opportunity case-start
   transaction.
5. Wiring stage-owned LangGraph `interrupt()` calls into the remaining
   production business handlers; the authenticated resume guard is implemented,
   and A1 currently returns a typed clarification interrupt at `A1.80`.
6. API/MCP/scheduler/dispatcher surfaces and production deployment.

Accordingly, this is a tested control-plane, contract, deterministic pilot-flow
and A1-intake implementation, not a working optimizer. P1/P2 code gates are
present; environment acceptance evidence and the remaining P3 production
business-node handlers are intentionally separate release gates.
