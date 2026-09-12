# 04. Orchestration Frame — As Built

Assessment date: 2026-09-12  
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

This increment implements orchestration and a deterministic pilot runtime, and
all 99 catalog task IDs now also have real, tested production handlers spanning
both lanes: `application/{a1,a2,a3,b1,b2,c0}_handlers.py`. Lane A
(`build_a1_runtime`/`build_a2_runtime`/`build_a3_runtime`) genuinely executes
repository commands (git, pytest, ruff, `act`-replayed CI, pytest-benchmark) via
`LocalWorkerBroker`, calls a real model provider (Anthropic/OpenAI/Gemini/
DeepSeek/Ollama) for finding/strategy generation, and is runnable end to end
against any real repository via `scripts/optimize.py`. Lane B
(`build_b1_runtime`/`build_b2_runtime`) mounts the same real A2/A3 registrations
at B1.95/B2.22 rather than a stand-in, and C0 (`build_c0_runtime`) performs real
schema/digest-chain/semantic-equivalence/freshness checks and computes its
convergence gate rather than asserting it. `NodeRuntime` still has no default
handler: a production invocation without an explicit registration for a node
still raises `NodeNotEnabledError`. The separate `build_pilot_runtime()`
registration remains available as local validation code: it drives all
documented nodes through LangGraph with strict compact artifact references and
keeps source, workers, external telemetry, and model providers untouched —
useful for topology/routing tests that should not depend on real I/O.

Two known, deliberate gaps remain, not oversights: B1.32-35 (the four
historical-data query nodes) have no real metrics/logs/traces/LLM-evidence
adapter wired into `NodePorts` yet, so they always report an honest
`unavailable_reason` rather than fabricate evidence (BR-B1-011: a no-op scan is
success, not failure); and Lane B has no scheduler/CLI entrypoint analogous to
Lane A's `scripts/optimize.py` — it has only been exercised end to end through
`tests/contract/test_b1_production_handlers.py`/`test_b2_production_handlers.py`,
which seed state directly and invoke the compiled subgraphs.

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
- A2/A3/B1/B2/C0 production handlers follow the same universal execution
  protocol (intent idempotency, artifact-store sealing, telemetry) and never
  re-seal a shared artifact node-by-node: multi-node accumulation (B1.40-81's
  `DetectionReport`, B2's working `b2_*` state fields) goes through plain
  reducer-guarded state fields, sealed exactly once at the owning node, so the
  `merge_artifact_refs` digest-conflict guard is never tripped.
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
- `manifests/node-manifest.json` contains all 99 unique task IDs. Every entry now
  has a real, registered, tested handler; none remain disabled.

## Implemented Files

| Area | Files |
|---|---|
| Commands | `contracts/commands.py` |
| Compact state/reducers | `contracts/state.py` |
| Handler boundary | `application/node_runtime.py` |
| Lane A production handlers | `application/{a1,a2,a3}_handlers.py`, `application/a2_worker_capabilities.py`, `tests/contract/test_{a1,a2,a3}_production_handlers.py` |
| Lane B production handlers | `application/{b1,b2}_handlers.py`, `tests/contract/test_{b1,b2}_production_handlers.py` |
| Shared convergence handlers | `application/c0_handlers.py`, `tests/contract/test_c0_production_handlers.py` |
| Human-in-the-loop resume | `application/resume.py` |
| Model provider adapters | `adapters/production/{anthropic,openai,deepseek,ollama,gemini}_model_provider.py`, `adapters/production/_openai_compatible.py`, `ports/model_provider.py` |
| Real end-to-end Lane A CLI | `scripts/optimize.py`, `scripts/_lane1_common.py` |
| Pilot flow handlers | `application/pilot_handlers.py`, `tests/contract/test_pilot_flow.py` |
| Durable checkpoint provider | `adapters/production/postgres_checkpoint.py` |
| Control-plane adapters | `adapters/production/{postgres_*,s3_artifact_store,jwt_identity,python_policy,otel_telemetry,local_worker_broker,kubernetes_worker_broker}.py` |
| Canonical contracts | `contracts/{a1,a2,a3,b1,b2,c0,registries}.py` |
| Schema registry/generator | `contracts/schema_registry.py`, `scripts/generate_contract_assets.py`, `schemas/` |
| Registry resolution | `application/registry.py` |
| Node manifest | `orchestration/manifest.py`, `manifests/node-manifest.json` |
| Canonical inventory | `orchestration/catalog.py` |
| Business graphs | `orchestration/subgraphs/{a1,a2,a3,b1,b2,c0}.py` |
| Lane composition | `orchestration/lanes.py` |
| Root composition | `orchestration/root_graph.py` |
| Contract evidence | `tests/contract/test_root_graph.py`, `tests/unit/test_commands.py` |

## Deliberately Not Claimed

The following remain real, currently-accepted gaps — not oversights, and not
simulated:

1. Production HA/failover, restore, KMS/object-lock and Kubernetes sandbox/network
   acceptance evidence; these require the target infrastructure.
   `KubernetesWorkerBroker` itself is implemented and unit-tested (restricted
   Job spec, idempotent submission, reconciliation, cancellation), but it has
   never been run against a live cluster and no script wires it up —
   `LocalWorkerBroker` is what Lane A/B actually execute against today.
2. A production secret-manager adapter; only secret-reference contracts exist
   (model-provider API keys are read via `EnvSecretsBroker` for local/CLI use).
3. B1.32-35's real metrics/logs/traces/LLM-execution-evidence query adapters:
   `NodePorts` has no such port wired in yet, so these four nodes always report
   an honest `unavailable_reason` instead of fabricating evidence. A real
   `PolicyPort` beyond `DeterministicPythonPolicy` (an OPA HTTP adapter) is
   likewise deferred.
4. B1 multi-candidate `Send` fan-out and atomic per-opportunity case-start
   transaction.
5. A scheduler/CLI entrypoint for Lane B analogous to Lane A's
   `scripts/optimize.py`. Lane B's real handlers have only been exercised
   end to end through `tests/contract/test_b1_production_handlers.py` /
   `test_b2_production_handlers.py`, which seed state directly and invoke the
   compiled subgraphs; nothing in this repository triggers
   `build_lane_b_discovery_graph` automatically (no cron, webhook or watcher).
6. Differentiated C0 failure routing. The convergence doc describes routing a
   digest mismatch back to the producer stage, a missing eligible solution back
   to A3/B2, and an obsolete source to close/version the case; the compiled
   graph (`orchestration/subgraphs/c0.py`) only has two outcomes at C0.60
   (`continue`/`rejected`→END) — every failure reason is recorded in
   `ConvergenceDecision.reasons`, not via a distinct graph edge.
7. API/MCP surfaces and production deployment.

Accordingly, this is a tested control-plane, contract and orchestration
implementation with real, working business-node handlers for all 99 catalog
task IDs across both lanes and the shared convergence gate — runnable end to
end for Lane A via `scripts/optimize.py` against a real repository. It is not
yet a fully automated, always-on system: Lane B needs a real trigger surface,
several ports (query adapters, OPA, Kubernetes) still have no production
caller, and environment acceptance evidence (HA, restore, cluster sandboxing)
remains a separate release gate from the code itself.
