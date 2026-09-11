# 06. Lane 2 Detailed Implementation Playbook

Status: normative implementation specification  
Scope: automatic origin, `B1 -> shared A2(historical_recovery) -> B2 -> shared
A3(automatic) -> C0`  
Prerequisite: Lane 1 shared A2/A3 contracts, policies and handlers have passed
their production gates  
Business authority: `docs/project-blueprint/lane-b-local-codebase/*` and
`docs/project-blueprint/shared-workflow/00-convergence.md`

## 1. Purpose and Product Boundary

Lane 2 detects optimization opportunities from existing historical evidence,
binds them to a registered feature/source/owner, reconstructs the same business
contract used by Lane 1, and prepares an A3-quality proposal. It does not run a
discovery benchmark, silently infer missing identity, edit code or become more
autonomous merely because initiation is automatic.

The only valid runtime chain is:

```text
StartDiscoveryScan -> B1 scan thread
  -> zero or more candidate branches
  -> B1.95 shared A2(mode=historical_recovery)
  -> B1.96 QualifiedOpportunity + transactional outbox
  -> StartQualifiedCase -> one isolated B2 case thread per opportunity
  -> B2.22 shared A3(origin=automatic)
  -> B2 proposal/approval
  -> C0 -> ConvergedCase
```

A no-data scan is a successful audited result. It must not create a synthetic
case merely to demonstrate activity.

## 2. Lane 2 Dependencies and Reuse Contract

Lane 2 implementation starts only after:

1. P1/FRAME-0 and P2 gates pass.
2. Repository, feature, owner, metric, workload, query and collector registries
   have production owners and data-quality rules.
3. Shared A2 and A3 are callable through compiled subgraphs, not service copies.
4. A2 accepts a typed mode and tests prove the mode changes collection routes,
   never quality/provenance/comparability policy.
5. A3 accepts typed origin/context and tests prove identical citation, maturity,
   strategy and quality gates for both origins.
6. Transactional outbox and dispatcher agree on signed `CaseStartRequested`,
   idempotency, retry, dead-letter and reconciliation.
7. Discovery thresholds are backtested before any user-visible proposal.

Lane 2 may add adapters for historical queries and scheduling. It may not fork
canonical contracts, A2/A3 handlers, policies, prompts or quality thresholds.

## 3. Scan and Case Isolation

| Identity | Lifetime | May contain | Must not contain |
|---|---|---|---|
| `scan_id`/scan `thread_id` | One frozen tenant/window/policy scan | source inventory, run groups, signals, candidate refs and dispositions | B2 approval, case budget or mutable source |
| Candidate fingerprint | One feature/metric/signal/source/window identity | merged evidence and dedup/cooldown decision | Another candidate's state |
| `case_id`/case `thread_id` | One qualified opportunity | immutable B1 handoff, A3 output, proposal and approval | Other candidates or scan progress |

B1.96 commits the qualified artifact and outbox record atomically. Dispatcher
redelivery maps to the same case/thread. The dispatcher authenticates transport
and invokes the root `qualified` entrypoint; it cannot select B2 internals.

## 4. Mandatory Delivery Sequence

```text
L2-00 registry and policy readiness
-> L2-10 scan control/source verification
-> L2-20 historical queries and normalization
-> L2-30 deterministic detection and qualification
-> L2-40 request reconstruction and shared A2 recovery
-> L2-50 outbox and isolated-case dispatch
-> L2-60 B2/shared A3 proposal
-> L2-70 full C0 equivalence
-> L2-80 shadow, reviewed release and production hardening
```

### L2-00 — Freeze discovery behavior

1. Define scan cadence, non-overlap rule, maximum window, deadline and tenant cost.
2. Register each allowed repository path, historical deployment identity rule,
   feature mappings, accountable owners and escalation owner.
3. Register read-only endpoints, query templates, allowed parameters, maximum
   range/cardinality and redaction profiles; secrets remain references.
4. Version metrics, units, material dimensions, minimum samples, freshness and
   trust promotion rules.
5. Version threshold, effect-size, scoring, qualification, deduplication and
   cooldown policies.
6. Build a reviewed incident/backtest corpus containing true positives, noise,
   seasonality, missing dimensions, stale source, duplicate and owner ambiguity.
7. Define precision, recall, false-proposal, suppression and cost SLOs.

Exit: every automatic decision can name its policy version and explain its input
facts; no query accepts free-form unbounded text from a trigger or model.

## 5. B1 — Automatic Discovery

### B1.1 Scan control and source verification

| Order/ID | Required implementation | Output | Decision/route |
|---:|---|---|---|
| 1 — B1.10 | Authenticate scheduler/service command; create scan/thread; freeze tenant, window, policy and budget; enforce overlap rule | Discovery scan context | Duplicate-equivalent trigger reuses scan; conflicting overlap is rejected/merged by policy |
| 2 — B1.20 | Load tenant-approved repository/feature/owner registrations at pinned versions; canonicalize and check accessibility | Registered source set | Missing/disabled/stale registration is quarantined, not queried |
| 3 — B1.21 | Fingerprint Git revision, dirty state and bounded content; compare registered/deployment identity | Observed source identity refs | Changed current source remains current fact; never substitute it for historical revision |
| 4 — B1.30 | Inventory available metrics/logs/traces/profiles/benchmarks/LLM evaluation windows without executing workloads | Historical source inventory | Unavailable source is recorded with reason; zero sources may end `NO_DATA` |
| 5 — B1.31 | Authorize endpoint, query template, parameters, time/range/cardinality, tenant and read-only credentials | Read authorization refs | Denied queries do not execute; protected fields are redacted after protected raw capture where permitted |

### B1.2 Historical query fan-out and normalization

| Order/ID | Required implementation | Output | Decision/route |
|---:|---|---|---|
| 6 — B1.32 | Execute registered bounded metrics queries for the frozen window | Historical metric sample refs | Partial endpoint failure follows mandatory/optional source policy |
| 7 — B1.33 | Execute structured log queries; require stable labels/fingerprint/trace IDs where configured | Historical log event refs | Free-text-only result is low trust and redacted before model access |
| 8 — B1.34 | Query traces/profiles; capture span/frame coverage and deployment/source correlation | Historical runtime evidence refs | Instrumentation gaps stay explicit |
| 9 — B1.35 | Query model/prompt/token/cost/latency/evaluation history when the registered feature is LLM-backed | Historical LLM evidence refs | Dataset/scorer/model/prompt version mismatch separates run groups |
| 10 — B1.40 | Preserve raw records, normalize registered units and group by all material identities | Historical run-group refs | Missing material keys downgrade/exclude; no LLM backfill |
| 11 — B1.41 | Apply schema, source, freshness, trust and minimum-sample eligibility | Eligible run set and exclusion report | No eligible groups -> successful `NO_DATA` scan branch |

Fan-in identity is `(source_kind, query_id, window, registry_version)`. Ordering is
stable. A successful optional query never hides a failed mandatory query.

### B1.3 Detection, binding and qualification

| Order/ID | Required implementation | Output | Decision/route |
|---:|---|---|---|
| 12 — B1.50 | Apply absolute SLO/guardrail/budget thresholds | Threshold signals | Symptom only; no causal wording |
| 13 — B1.51 | Compare compatible windows; quantify direction, effect and practical significance | Regression signals | Seasonal/workload differences remain separate or ineligible |
| 14 — B1.52 | Aggregate repeated slow spans/profile frames/error fingerprints/resource saturation | Hotspot/failure signals | Frequency does not establish root cause |
| 15 — B1.53 | Detect quality drift, scorer failure, token/cost/latency inflation with stable cohorts | LLM opportunity signals | Scorer/dataset drift is a competing explanation, not product degradation |
| 16 — B1.60 | Resolve feature by explicit label, then trace/service/symbol registration; retain alternatives/confidence | Feature binding | Ambiguous mapping -> quarantine/owner interrupt |
| 17 — B1.61 | Bind deployment/trace commit to available local Git object/content snapshot | Source binding | Historical revision unavailable -> `SOURCE_UNRESOLVED`; current HEAD is forbidden fallback |
| 18 — B1.62 | Resolve code, service and decision owners; detect disagreement and escalation | Ownership binding | Missing/conflicting accountable owner -> quarantine/interrupt |
| 19 — B1.70 | Deterministically score severity, frequency, impact, trust, addressability and priority | Opportunity score | Score inputs/weights/version are auditable |
| 20 — B1.71 | Apply hard trust, identity, samples, actionability and policy gates | Qualification decision | High score never overrides a hard failure |
| 21 — B1.80 | Compute deterministic candidate fingerprint; compare active/recent cases; merge only same identity | new/merged/suppressed decision | Merge adds immutable evidence; never merges unrelated causes by narrative similarity |
| 22 — B1.81 | Apply cooldown, expiry, severity/material-change override and suppression audit | Cooldown decision | Suppression remains observable and reviewable |

Detector fan-out uses `B1.50`–`B1.53`. Multiple signals may create multiple
candidate branches using bounded LangGraph `Send`. Candidate output is reduced by
fingerprint, never array position or completion time.

### B1.4 Reconstruct, recover and publish

| Order/ID | Required implementation | Output | Decision/route |
|---:|---|---|---|
| 23 — B1.90 | Construct `OptimizationRequest@1.0` origin `automatic` from verified facts and registries; fill every A1 field | Automatic request ref | Unknown mandatory field blocks; no requester exists to justify guessing |
| 24 — B1.91 | Apply automatic-intake policy; request owner confirmation with digest-bound interrupt when needed | Approval decision/ref | Auto-accept, exact-thread owner interrupt or reject |
| 25 — B1.95 | Invoke the exact compiled A2 subgraph with `historical_recovery` | Baseline/evidence/quality/comparability refs | Missing history requests targeted evidence or closes; discovery benchmark is forbidden |
| 26 — B1.96 | Seal scan/signal/request/baseline/owner/policy chain and atomically enqueue deterministic case start | QualifiedOpportunity ref and outbox record | Commit both or neither; redelivery must map to same case/thread |

## 6. Shared A2 `historical_recovery` Contract

Every A2 node remains present and checkpointed. Mode-specific behavior is limited
to these explicit collector choices:

| A2 area | Historical-recovery behavior | Rule unchanged from Lane 1 |
|---|---|---|
| A2.10 | Verify automatic request and B1 provenance | Schema, digest, approval and policy validation |
| A2.20 | Reproduce exact historical local source | Content-addressed snapshot and source-change handling |
| A2.30–A2.31 | Build manifest/check definitions without execution | Commands must be repository-owned and resolvable |
| A2.40–A2.41 | Bind historical collectors and recorded environments | All material dimensions required |
| A2.50 | Authorize read/import work only | Tenant, secret, network, resource and budget controls |
| A2.60 | Use existing static evidence only if identity-valid; otherwise optional unavailable | Coverage remains visible |
| A2.61 | Import eligible correctness history | Missing tests are not passing |
| A2.62 | Import registered historical performance samples; do not launch workload | Raw samples and workload identity required |
| A2.63 | Import historical telemetry as primary source | Query/provenance/freshness controls unchanged |
| A2.64 | Reconstruct source/runtime map | Uncertainty remains explicit |
| A2.70–A2.95 | Preserve, normalize, bind, quality-check, compare and seal | Identical transformations, trust and gates |

One `NodeSpec`/handler identity may branch internally on typed mode only where
listed. Separate “B1 baseline” handlers, schemas or relaxed thresholds are forbidden.

## 7. Outbox and Dispatcher Protocol

1. B1.96 starts one PostgreSQL transaction.
2. Insert/create-only the qualified artifact reference and deterministic case
   identity in control-plane records.
3. Insert outbox event keyed by tenant plus candidate fingerprint/version.
4. Commit; only committed rows are dispatchable.
5. Dispatcher leases a row, signs the internal command and invokes root
   `StartQualifiedCase`.
6. Root authenticates service identity and verifies tenant, digest, event and
   idempotency before selecting `qualified`.
7. Successful durable graph acceptance marks delivery complete.
8. Timeout/unknown response reconciles by command/case ID before redelivery.
9. Permanent invalid command moves to dead-letter with diagnostic artifact.

At-least-once delivery is expected; exactly-once business effect is achieved by
idempotent acceptance and durable identity, not by assuming one delivery.

## 8. B2 — Automatic Proposal

| Order/ID | Required implementation | Output | Decision/route |
|---:|---|---|---|
| 1 — B2.10 | Verify opportunity schema/digests, unique case binding, source/owner availability, age and baseline comparability before spending budget | B2 intake decision | Stale/duplicate/invalid -> refresh or close |
| 2 — B2.20 | Select applicable analyzers using language, evidence, topology, risk, registry availability and budget | Analysis strategy | Skips include explicit reason and coverage |
| 3 — B2.21 | Build bounded/redacted context with evidence IDs, relevant source regions, counterevidence, criteria and truncation report | Model context package | Disallowed classification/secret/raw oversize -> deny or reduce visibly |
| 4 — B2.22 | Invoke the exact compiled A3 subgraph with origin `automatic` | FindingSet, SolutionPortfolio, A3QualityReport refs | Identical A3 gates; no prompt-only shortcut |
| 5 — B2.30 | Recheck feature/source/owner bindings; label detected facts versus inferred discovery assumptions | Discovery assumption report | Unverified material assumption blocks implementation-eligible proposal |
| 6 — B2.31 | Compare current source, registry, evidence and policy versions with the B1 handoff | Staleness decision | Fresh -> proceed; material change -> refresh/new case; obsolete -> cancel |
| 7 — B2.40 | Consolidate measured impact, evidence, cause maturity, unknowns, eligible/ineligible strategies, tradeoffs, risk and quality | ProposalEnvelope ref | Structured omissions are hard failures |
| 8 — B2.41 | Produce concise explanation from sealed structured facts | Narrative ref | Narrative cannot change score, confidence, eligibility or hide rejected items |
| 9 — B2.50 | Route by risk, security, confidence, ownership and policy | Proposal routing decision | auto-forward, owner review, security review or reject |
| 10 — B2.51 | Interrupt exact thread with proposal digest, decisions, required role, policy and expiry | Approval ref | Resume must pass complete binding; bare approval is invalid |
| 11 — B2.52 | Apply rejection/cooldown or target only requested A3/B2 dimensions for bounded revision | Revised refs or closure | Preserve unaffected artifacts and all attempts; enforce cost/retry cap |
| 12 — B2.60 | Seal proposal, approval and B1/A3 lineage; emit C0 handoff | Lane B handoff ref | Acceptance allows convergence/selection only, never source modification |

### Shared A3 equivalence

B2.22 uses the same node IDs `A3.10`–`A3.90`, handlers, schemas, analyzer policy,
citation resolver, judge rules, maturity rules and portfolio gate as Lane 1.
Automatic context adds B1 bindings and discovery assumptions but cannot remove A1
criteria, A2 evidence, counterevidence, disclosure controls or quality checks.

## 9. Full C0 Equivalence for Lane 2

| Node | Additional automatic-origin proof |
|---|---|
| C0.10 | Origin remains `automatic` from the qualified-case command |
| C0.20 | QualifiedOpportunity and ProposalEnvelope plus canonical A1/A2/A3 artifacts validate |
| C0.30 | Scan -> opportunity -> request/baseline -> A3 -> proposal parent chain is intact |
| C0.40 | Reconstructed request satisfies every A1 semantic field; recovered evidence satisfies identical A2 gates; proposal satisfies identical A3 gates |
| C0.50 | Source, evidence, owner, approval, registry and policy versions remain fresh |
| C0.60 | Comparable baseline, passed A3 quality, eligible solution and required approval all pass |
| C0.70 | Publish the same `ConvergedCase@1.0` shape consumed for Lane 1 |

C0 code and policy remain one implementation. Origin is retained for audit and
freshness rules, never used to lower quality.

## 10. Lane 2 Failure and Disposition Matrix

| Condition | Candidate/scan disposition | May create B2 case? |
|---|---|---:|
| No historical data or no eligible run group | `NO_DATA`, successful scan | No |
| Temporary optional endpoint outage | Partial with visible failure | Only if mandatory coverage still passes |
| Unresolved historical source | Quarantine `SOURCE_UNRESOLVED` | No |
| Ambiguous feature or owner | Quarantine/interrupt | No until resolved |
| Hard qualification failure | Rejected with policy reasons | No |
| Duplicate active case | Merge immutable evidence | No new case |
| Cooldown match | Suppressed with expiry/override facts | No |
| Incomplete recovered baseline | Targeted evidence request or close | No |
| Outbox delivery unknown | Reconcile then redeliver same identity | Same case only |
| Opportunity stale before B2/A3 | Refresh/new version or obsolete close | No analysis on stale input |
| No eligible A3 solution | Close or targeted evidence/revision | No C0 handoff |
| Owner rejection | Close and feed deterministic cooldown | No C0 handoff |

## 11. Security and Data Controls

- Scheduler and dispatcher use separate least-privilege service identities.
- Historical adapters receive read-only credentials with short leases and fixed
  endpoint/query policy.
- Query parameters are typed; raw trigger/model text cannot become a query.
- Tenant, time range, returned cardinality, bytes and cost are enforced before and
  after each query.
- Raw protected data is access-controlled in the artifact store; model context is
  separately redacted and bounded.
- Repository access is read-only; historical recovery may not fetch arbitrary code
  from unapproved network locations.
- Candidate artifacts and logs do not expose source, evidence or secrets.
- Owner approval and internal case-start commands are digest-bound and auditable.

## 12. Test Strategy

### Contract and graph

- All 26 B1 and 12 B2 IDs occur once; B1.95 and B2.22 reference shared compiled
  A2/A3 implementations.
- Scan/case state cannot overwrite each other; reducers are deterministic.
- Automatic artifacts validate the same schema major and C0 semantics as manual artifacts.

### Discovery correctness

- No-data, partial-source, missing-dimension and stale-window fixtures.
- Backtested threshold/regression/hotspot/LLM signals with known expected results.
- Feature/source/owner ambiguity and historical revision unavailable cases.
- Dedup merge, cooldown expiry, severity override and multi-candidate isolation.

### Reliability

- Restart during query fan-out, candidate fan-out, owner interrupt and B2 approval.
- Crash before/after B1.96 commit; outbox redelivery and dispatcher unknown response.
- Worker/query timeout, database/object-store outage and dead-letter reconciliation.
- Repeated trigger/dispatch produces no duplicate scan, opportunity or case effect.

### Security

- Cross-tenant registry/query/artifact/case denial.
- Query injection, cardinality/range abuse, credential leakage and protected-field redaction.
- Prompt injection in logs/source cannot alter tools, routes, policy or citations.
- Wrong actor/role/digest/policy/expiry cannot resume an interrupt.

### Equivalence

- Paired manual/automatic fixtures representing the same facts converge to equivalent
  request, evidence and portfolio semantics.
- Deliberately weaker automatic criteria, provenance, maturity or tradeoffs fail C0.40.
- Source/owner/policy changes between B1, B2 and C0 trigger refresh or closure.

## 13. Rollout and Observability

Promotion is deliberately asymmetric:

```text
offline backtest
-> shadow scans with no proposal/outbox
-> reviewed candidate reports
-> outbox/B2 in non-production tenant
-> production shadow with owner-only visibility
-> owner-approved proposals
-> controlled suggest-only production
```

Dashboards must separate scans, candidates and cases and include query latency/cost,
eligible/excluded groups, signal count, precision review, qualification, quarantine,
dedup, suppression, outbox lag/redelivery, stale proposals, approval wait, A2/A3
reuse failures, C0 rejection and tenant budget consumption.

Rollback disables future schedules and dispatch leases without deleting scans,
opportunities, cases or evidence. In-flight cases follow explicit cancel/reconcile
policy; disabling Lane 2 never corrupts shared A2/A3 for Lane 1.

## 14. Lane 2 Release Gates

| Gate | Required evidence |
|---|---|
| L2-G1 registry/query | Versioned registry, read-only authorization, bounded-query security tests |
| L2-G2 discovery | Reviewed backtest meets agreed precision/noise/cost thresholds |
| L2-G3 isolation | Zero/one/many candidates produce deterministic independent dispositions |
| L2-G4 A2 reuse | Historical recovery performs no discovery workload and passes identical gates |
| L2-G5 transport | Atomic seal/outbox and redelivery create exactly one case identity |
| L2-G6 A3 reuse | Same compiled A3 identity and policy; stale input spends no model budget |
| L2-G7 C0 | Weaker automatic fixtures fail; equivalent fixtures produce common handoff |
| L2-G8 production | Shadow review, tenant isolation, load/chaos, runbook and rollback drill pass |

## 15. Lane 2 Definition of Done

Lane 2 is done only when scheduled scans safely produce zero, one or many fully
accounted candidate dispositions; every qualified opportunity has verified source,
feature and owner plus an A1-equivalent request and A2-equivalent real baseline;
outbox delivery is idempotent; B2 uses the exact shared A3 implementation; stale or
weak cases fail closed; C0 emits the same lane-neutral contract; and operations can
pause, inspect, resume, reconcile and audit without manual data mutation.

