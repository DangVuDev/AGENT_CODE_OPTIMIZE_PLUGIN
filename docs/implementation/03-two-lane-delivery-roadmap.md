# 03. Two-Lane Product Delivery Roadmap

Status: implementation sequence; orchestration frame, deterministic pilot flow and A1 production-intake slice implemented; remaining production business handlers pending  
Architecture: [`02-two-lane-product-architecture.md`](02-two-lane-product-architecture.md)  
Business authority: `docs/project-blueprint/lane-a-local-codebase`,
`lane-b-local-codebase`, and `shared-workflow/00-convergence.md`

Detailed, normative execution playbooks:

- [`05-lane-1-detailed-implementation-playbook.md`](05-lane-1-detailed-implementation-playbook.md)
- [`06-lane-2-detailed-implementation-playbook.md`](06-lane-2-detailed-implementation-playbook.md)

## 1. Delivery Strategy

Build one production control plane, one shared evidence pipeline and one shared
grounded-analysis pipeline. Deliver Lane A first as the vertical reference,
then add Lane B discovery around the same A2/A3 implementations, and finally
prove equivalence at C0.

```text
P0 scaffold (complete)
-> P1 durable control-plane foundation
-> P2 canonical contracts and registries
-> P3 Lane A: A1 -> shared A2 -> shared A3 -> C0
-> P4 Lane B: B1 -> shared A2 -> B2/shared A3 -> C0
-> P5 product surfaces and two-lane hardening
```

Do not develop Lane A and Lane B as two services with duplicate schemas or
analysis logic. Do not start with an LLM. The first executable product path is
identity -> policy -> snapshot/evidence -> deterministic gates; model providers
are attached only after evidence integrity and budgets work.

## 2. Definition of Ready for Every Node

Before a task node enters implementation, it must have:

1. A catalog task ID and owning compiled subgraph.
2. Pydantic input/output contracts and supported schema major versions.
3. Preconditions, postconditions and explicit route keys.
4. A versioned idempotency-key derivation.
5. Side-effect class, timeout, cancellation, retryable errors and reconciliation
   or compensating action.
6. Artifact writes, parent digests and audit event.
7. Framework/capability port and a rule identifying the deterministic decision
   authority.
8. Security/data classification and prompt-disclosure boundary.
9. SLI/SLO, metric attributes, owner and runbook.
10. Unit, contract, integration, failure and resume scenarios.

If any item is absent, the node remains disabled in the graph manifest.

## 3. Standard Node Implementation Method

Implement each node in this order:

```text
contract and schema fixture
-> pure application handler using ports
-> policy/gate golden tests
-> adapter conformance fixture
-> LangGraph node wrapper
-> intent/idempotency/artifact/event integration
-> conditional-edge tests
-> failure/replay/interrupt tests
-> telemetry and runbook evidence
-> enable node in environment capability policy
```

Handlers return typed outcomes; wrappers alone map outcomes to graph edges.
Provider retries use LangGraph retry policy or the worker protocol—not hidden
SDK loops. Side effects follow intent -> execute/reconcile -> artifact -> state
commit. An unavailable adapter produces a typed disabled/unavailable route, not
a placeholder success.

## 4. Target Source Organization

```text
src/production_optimizer/
|-- contracts/
|   |-- envelope.py, state.py, platform.py
|   `-- a1.py, a2.py, a3.py, b1.py, b2.py, c0.py
|-- application/
|   |-- node_runtime.py, artifacts.py, idempotency.py, routing.py
|   `-- handlers/
|       |-- a1/, a2/, a3/, b1/, b2/, c0/
|-- orchestration/
|   |-- root_graph.py, commands.py, reducers.py
|   `-- subgraphs/a1.py, a2.py, a3.py, b1.py, b2.py, c0.py
|-- ports/
|   |-- registries.py, collectors.py, analyzers.py, models.py
|   `-- existing platform ports
|-- adapters/
|   |-- postgres/, object_store/, policy/, identity/, kubernetes/
|   |-- git/, telemetry/, analysis/, models/
|   `-- development/
|-- api/, mcp/, scheduling/, observability/
`-- composition.py

schemas/{canonical,internal}/
policies/{intake,evidence,discovery,analysis,convergence}/
migrations/
deploy/{development,production}/
tests/{unit,contract,integration,resume,chaos,security,e2e}/
docs/runbooks/{platform,a1,a2,a3,b1,b2,c0}/
```

Closely related handlers may share a module, but every task retains a separate
callable, `NodeSpec`, graph node name and test identity.

## 5. P0 — Pre-Node Scaffold

Current as-built scope is recorded in
[`01-project-scaffold.md`](01-project-scaffold.md).

Exit already demonstrated:

- locked dependency environment;
- loadable root export (subsequently expanded by the orchestration-frame increment);
- compact state and infrastructure ports;
- migration and local infrastructure definitions;
- tests preventing premature A1/A2/A3 registration; and
- lint, strict types and coverage gates.

Remaining blocker: organization Git remote, CODEOWNERS and branch/release
governance.

## 6. P1 — Durable Control-Plane Foundation

No business node is enabled in this phase.

| Order | Deliverable | Framework | Required proof |
|---:|---|---|---|
| 1 | PostgreSQL checkpoint provider and migrations | `langgraph-checkpoint-postgres` | setup, strict serialization, HA/failover and restore tests |
| 2 | Case, intent ledger and transactional outbox adapters | PostgreSQL | concurrent idempotency, leases, unknown-outcome reconciliation |
| 3 | Immutable artifact/CAS adapter | S3/MinIO-compatible store + KMS | create-only, digest/signature, tenant isolation, retention |
| 4 | Identity and policy adapters | Host IdP/RBAC + OPA or Python policy port | cross-tenant denial, policy version and golden decisions |
| 5 | Worker broker and sandbox contract | Kubernetes Job + gVisor/equivalent | deny egress, short secrets, timeout/cancel/cleanup/reconcile |
| 6 | Control-plane telemetry | OpenTelemetry SDK/Collector | required attributes, no raw source/evidence/secrets |
| 7 | Root command/runtime frame | LangGraph | crash-before/after-effect, interrupt/resume, dead-letter |

Exit gate `P1` equals `FRAME-0`: the runtime survives restart and proves no
duplicate side effects before any business logic is added.

## 7. P2 — Canonical Contracts and Registries

Implementation order:

1. Canonical envelope, artifact reference, signature, event, error and interrupt
   contracts.
2. A1/A2/A3/B1/B2/C0 Pydantic models and generated JSON Schemas.
3. Compatibility fixtures and stable canonical-JSON digest fixtures.
4. Repository, feature, owner, metric, workload, query, collector, analyzer,
   policy and model-provider registries.
5. Redaction classification and bounded-context contracts.
6. Graph manifests with all 99 unique task IDs disabled until their handlers
   pass Definition of Ready.

Required ADRs before P2 exit:

- repository/governance and release ownership;
- Python/LangGraph/checkpointer versions;
- checkpoint/case schema isolation and retention;
- object store, KMS and canonical serialization;
- OPA versus deterministic Python policy boundary;
- scan-thread/case-thread/outbox design;
- worker transport and sandbox;
- first supported language/repository profile;
- analyzer selection per language; and
- generator/judge provider, disclosure and fallback policy.

## 8. P3 — Lane A Reference Implementation

### P3.1 A1 manual intake

| Wave | Nodes in business order | Implementation focus | Frameworks |
|---|---|---|---|
| A1-I | A1.10, A1.20 | Authenticated intake, raw/structured extraction, unknown preservation | LangGraph, Pydantic, approved LLM only for raw extraction |
| A1-S | A1.30, A1.40, A1.50 | Allowed-root/Git identity, bounded project discovery, feature scope confidence | Git, Tree-sitter; optional CodeQL/Joern resolver |
| A1-C | A1.60, A1.61, A1.62, A1.63 | Objective, criteria, correctness guardrails, priority/budget | Pydantic/JSON Schema, OPA/Python policy |
| A1-E | A1.70, A1.71 | Frozen workload identity and evidence requirements | Registry, k6/MLflow/OTel capability metadata |
| A1-G | A1.80, A1.90, A1.95 | Ambiguity, policy/approval interrupt, immutable request | Deterministic policy, LangGraph interrupt, object store/KMS |

Exit: raw and structured scenarios work; missing criteria/workload interrupts;
dirty Git state is explicit; approvals bind actor, policy, expiry and digest.

### P3.2 Shared A2 active baseline

| Wave | Nodes in business order | Implementation focus | Frameworks |
|---|---|---|---|
| A2-H | A2.10, A2.20 | A1 verification and exact content snapshot | Pydantic, Git, immutable object store |
| A2-M | A2.30, A2.31 | Manifest, languages, tools and safe command discovery | Tree-sitter, Semgrep project awareness, native detectors |
| A2-P | A2.40, A2.41, A2.50 | Collector plan, material environment and authorization | Registries, OPA, secret/worker broker |
| A2-F | A2.60, A2.61, A2.62, A2.63, A2.64 | Parallel static/test/performance/telemetry/source-map evidence | Native tests, Testcontainers, k6/Bencher, OTel stack, applicable analyzers |
| A2-Q | A2.70, A2.71, A2.80, A2.90, A2.91, A2.95 | Raw preservation, normalization, provenance, quality, comparability and sealing | Object store, Pydantic, deterministic policy |

Implement only the adapters required by the approved pilot repository. An
unsupported optional analyzer reports coverage; a missing mandatory collector
blocks A3. A2.62 must run only repository-owned, policy-authorized argv inside
the sandbox.

### P3.3 Shared A3 grounded analysis

| Wave | Nodes in business order | Implementation focus | Frameworks |
|---|---|---|---|
| A3-I | A3.10, A3.11, A3.20, A3.21 | Verify handoff, catalogue evidence, deterministic signals and priority | Pydantic, Tree-sitter index, statistics, OPA/Python policy |
| A3-F | A3.30, A3.31, A3.32, A3.33 | Parallel pattern, semantic, domain and runtime analysis | Tree-sitter/Semgrep; conditional CodeQL/Joern/Slither; Tempo/Pyroscope |
| A3-J | A3.40, A3.41, A3.50, A3.51 | Typed finding draft, exact citation resolution, independent judging and maturity | Approved generator, deterministic resolver, separate judge, policy |
| A3-S | A3.60, A3.61, A3.62, A3.63, A3.64 | Diverse strategies, one-treatment phases, impacts, tradeoffs, validation/rollback | LLM generators/critic plus Pydantic and repository resolver |
| A3-G | A3.70, A3.80, A3.81, A3.82, A3.90 | Scope/risk/portfolio gates, targeted bounded revision and sealing | Tree-sitter/Semgrep/CodeQL, OPA/Python policy, LangGraph |

LLM nodes are last in this phase. Before enabling them, tests must prove a
collector failure cannot reach A3, nonexistent citations fail, negative-only
evidence cannot become a verified cause, and hypothesis-only work is
diagnostic-only.

### P3.4 Lane A C0 slice

Implement C0.10, C0.20, C0.30, C0.40, C0.50, C0.60 and C0.70 for manual origin
after A3.90. Initially only manual equivalence is enabled; C0.40 still owns one
common rule set and must not be forked later for Lane B.

P3 exit: one real pilot repository reconstructs request -> raw baseline ->
findings -> solutions -> `ConvergedCase`; every interrupt resumes; no source is
modified.

## 9. P4 — Lane B Discovery and Proposal

### P4.1 Registries and scan control

| Wave | Nodes | Implementation | Frameworks |
|---|---|---|---|
| B1-R | B1.10, B1.20, B1.21 | Freeze scan/window/policy; load and verify registered local sources | LangGraph, scheduler trigger, Git, Pydantic |
| B1-Q0 | B1.30, B1.31 | Inventory sources and authorize bounded read-only queries | Collector registry, OPA, secret/network policy |

The scheduler creates an idempotent `StartDiscoveryScan` command. Overlapping
tenant/window scans are rejected or merged by policy.

### P4.2 Historical query and normalization

| Wave | Nodes | Implementation | Frameworks |
|---|---|---|---|
| B1-Q1 | B1.32, B1.33, B1.34, B1.35 | Parallel existing metrics/logs/traces/profiles/LLM evidence queries | Prometheus, Loki, Tempo, Pyroscope, OTel, MLflow |
| B1-N | B1.40, B1.41 | Preserve raw records, normalize/group dimensions, exclude ineligible data | Object store, Pydantic/JSON Schema, deterministic policy |

B1 query adapters are read-only and cannot launch benchmark, test or analyzer
work. Query time, range, cardinality, tenant and cost limits are enforced before
submission.

### P4.3 Detection and qualification

| Wave | Nodes | Implementation | Frameworks |
|---|---|---|---|
| B1-D | B1.50, B1.51, B1.52, B1.53 | Parallel threshold, trend, hotspot/failure and LLM-quality detection | Statistics, Prometheus rules, Bencher concepts, Pyroscope/Tempo/Loki/MLflow |
| B1-B | B1.60, B1.61, B1.62 | Bind feature, exact historical source and accountable owner | OTel conventions, Tree-sitter maps, Git, owned registries |
| B1-G | B1.70, B1.71 | Score then apply non-overridable hard qualification | Deterministic scoring, OPA/Python policy |
| B1-C | B1.80, B1.81 | Deduplicate, merge evidence, cooldown and noise control | Case index/fingerprints, deterministic policy |

Backtest thresholds and weights against reviewed incidents before shadow-mode
release. Precision/recall and suppression outcomes become product metrics.

### P4.4 Reconstruct, recover and fan out cases

| Wave | Nodes | Implementation | Frameworks |
|---|---|---|---|
| B1-A | B1.90, B1.91 | Construct the canonical automatic `OptimizationRequest`; interrupt unresolved owner fields | Shared A1 validators, Pydantic, OPA, LangGraph interrupt |
| B1-E | B1.95 | Invoke A2 with `historical_recovery`; reuse quality/comparability unchanged | Exact compiled A2 subgraph |
| B1-P | B1.96 | Seal opportunity and atomically create case-start outbox | Object store/KMS, PostgreSQL transaction/outbox |

P4.4 exits only when every candidate is qualified, merged, suppressed,
quarantined or rejected. No-data scans are successful audited runs.

### P4.5 B2 proposal

| Wave | Nodes | Implementation | Frameworks |
|---|---|---|---|
| B2-I | B2.10, B2.20, B2.21 | Freshness/integrity, applicable analyzers and bounded/redacted model context | Pydantic, Git, analyzer registry, redaction policy |
| B2-A | B2.22 | Invoke exact compiled A3 subgraph with automatic origin | Shared A3 implementation and policies |
| B2-V | B2.30, B2.31 | Revalidate discovery assumptions, source, owner and evidence staleness | Deterministic resolver, Git/digests, optional independent critic |
| B2-P | B2.40, B2.41 | Complete structured proposal plus non-authoritative narrative | Templates/object store, LLM explanation |
| B2-R | B2.50, B2.51, B2.52 | Deterministic route, authenticated interrupt, targeted revise/reject | OPA/Python policy, LangGraph interrupt/edges |
| B2-H | B2.60 | Seal proposal and C0 handoff | Object store/KMS, audit event |

P4 exit: repeated scan/case-start deliveries create no duplicates; B2 uses the
same A3 compiled graph/policy identifiers; stale opportunities never spend model
budget; rejected cases feed deterministic cooldown without hiding evidence.

## 10. P5 — Full C0 and Product Surfaces

Complete C0.10, C0.20, C0.30, C0.40, C0.50, C0.60 and C0.70 for both origins
and expose:

- authenticated manual case create/inspect/follow/resume/cancel;
- source/feature/owner/query registry administration;
- scan schedule, status, suppression and quarantine review;
- opportunity/proposal structured review and artifact download authorization;
- signed internal case-start dispatch and reconciliation; and
- operations dashboards, alerts, runbooks and audit reconstruction.

Product API responses always return case/scan/thread IDs, current node, status,
interrupt envelope, artifact references and next action. They never block for a
complete workflow or return raw secret/source content by default.

## 11. Test and Release Matrix

| Layer | Mandatory suites |
|---|---|
| Graph | Exact node inventory, valid routes, subgraph identity, no coarse bypass, deterministic reducers |
| Contract | Schema compatibility, canonical names/versions, digest fixtures, redaction and migrations |
| Node | Handler unit, policy golden, port contract, timeout/retry/cancel, artifact/event and route |
| Lane A | Raw/structured intake, dirty source, missing workload, collector partial failure, incomparable evidence, invalid citation, no eligible solution |
| Lane B | No data, partial endpoint, ambiguous source/feature/owner, stale revision, duplicate/cooldown, multi-candidate scan, A2/A3 reuse identity |
| C0 | Cross-lane equivalent fixtures, digest mismatch, stale approval, weaker Lane B rejection |
| Reliability | Crash before/after effect, duplicate callback, process restart at every interrupt, outbox redelivery, worker loss |
| Security | Tenant/path escape, prompt injection, query abuse, credential leakage, egress/tool denial, artifact authorization |
| Operations | Load/SLO, checkpoint failover, backup/restore, object-store outage, policy/model circuit breaker, chaos drill |

## 12. Environment Promotion

```text
unit/contract
-> local integration
-> shadow discovery (B1 produces no visible proposals)
-> Lane A suggest-only pilot
-> Lane B shadow with reviewed backtest
-> Lane B proposals requiring owner approval
-> two-lane suggest-only production
```

Automatic implementation and rollout remain disabled because they belong to
shared steps 01–08. Lane B never becomes more autonomous merely because its
entry is automatic.

## 13. Final Definition of Done

The two-lane product slice is done when:

- all 99 unique catalog tasks and nested reuse routes exist in the compiled
  graph inventory;
- Lane A and B use the same canonical A2/A3 code, schemas and policies;
- a scan can produce zero, one or many isolated cases deterministically;
- raw evidence reconstructs every baseline and signal;
- unsupported claims and weak evidence fail closed before proposal publication;
- C0 produces the same `ConvergedCase` contract for both origins;
- restart, duplicate delivery and interrupts do not repeat side effects;
- all production adapters have approved ADRs and conformance evidence; and
- on-call can diagnose/reconcile through supported APIs and runbooks without
  manual database or artifact mutation.
