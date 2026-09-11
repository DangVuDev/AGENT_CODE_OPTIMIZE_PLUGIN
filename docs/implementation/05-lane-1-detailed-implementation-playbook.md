# 05. Lane 1 Detailed Implementation Playbook

Status: normative implementation specification  
Scope: manual origin, `A1 -> A2(active_collection) -> A3(manual) -> C0`  
Prerequisites: P1/FRAME-0 and P2 code gates; production infrastructure acceptance
is tracked separately  
Business authority: `docs/project-blueprint/lane-a-local-codebase/*` and
`docs/project-blueprint/shared-workflow/00-convergence.md`

## 1. Purpose and Product Boundary

Lane 1 turns an authenticated optimization request into a lane-neutral,
evidence-grounded `ConvergedCase`. It performs intake, real baseline collection,
problem analysis and solution proposal. It does not select a solution, modify
source, deploy or claim an improvement.

The only valid runtime chain is:

```text
CreateManualCase
-> OptimizationRootGraph
-> A1RequirementSubgraph
-> A2BaselineSubgraph(mode=active_collection)
-> A3SolutionSubgraph(origin=manual)
-> C0ConvergenceSubgraph
-> ConvergedCase
```

No API, service, worker or adapter may call a later business handler directly.
LangGraph owns all progress, branching, retry boundaries, interrupts and handoffs.

## 2. Business Definitions

| Term | Normative meaning |
|---|---|
| Requester | Authenticated actor that states the desired outcome and supplies source/workload context |
| Approver | Authenticated actor authorized by policy for the exact artifact digest |
| Objective | Desired measurable outcome, separated from any suggested implementation |
| Criterion | Primary metric, direction, target, unit and weight used to judge benefit |
| Guardrail | Non-regression constraint that must remain true |
| Workload | Reproducible command/scenario, dataset, environment, repetitions, concurrency and cache state |
| Evidence | Real observation with raw reference, provenance, source identity and trust level |
| Finding | Evidence-bound observation, hypothesis or verified cause |
| Strategy | Proposed mechanism with treatment phases, impact, tradeoffs, validation and rollback |
| Converged case | Fresh, digest-valid request/baseline/evidence/portfolio package accepted by C0 |

An LLM may extract, draft, correlate or explain. It never authenticates, authorizes,
validates its own citations, upgrades trust, declares a verified cause, changes a
hard gate or approves a case.

## 3. Ownership and Conflict-Prevention Rules

| Concern | Single authority | Forbidden competing authority |
|---|---|---|
| Workflow transition | LangGraph edge/conditional edge | Handler, adapter, API or worker choosing the next node |
| Compact case state | Owning node through `NodeExecution.updates` | Adapter mutating graph state |
| Raw source/evidence/model output | Immutable artifact store | PostgreSQL checkpoint or logs |
| Schema and version | Pydantic model and committed JSON Schema | Ad-hoc dictionaries or prompt-only schemas |
| Authorization | Identity port plus versioned policy decision | UI Boolean, model output or worker response |
| Registry identity | Tenant-scoped versioned registry | Path/name guessed from telemetry |
| Source identity | Content snapshot digest plus Git metadata | HEAD alone for a dirty tree |
| Retry | NodeSpec/LangGraph or worker protocol | Hidden SDK retry that changes identity |
| Approval | Digest-bound interrupt/resume contract | Bare `yes/no` or a new thread |
| A2/A3 implementation | Shared subgraph and shared handlers | Lane-specific copy or weakened policy |

Only a node may produce its declared output. Downstream nodes reference an
immutable digest and must never edit an upstream artifact. A material correction
creates a new artifact version with parent digests and re-enters the owning node.

## 4. Mandatory Delivery Sequence

Lane 1 is implemented in the following order. A later wave may be developed
behind a disabled manifest entry, but it cannot be enabled before every preceding
exit gate passes.

```text
L1-00 readiness and pilot freeze
-> L1-10 A1 intake
-> L1-20 A2 snapshot and manifest
-> L1-30 A2 collection and evidence gates
-> L1-40 A3 deterministic analysis foundation
-> L1-50 A3 model-assisted findings and strategies
-> L1-60 C0 manual convergence
-> L1-70 end-to-end, reliability, security and operational acceptance
```

### L1-00 — Freeze the implementation profile

Before implementing a handler:

1. Select one pilot repository and record allowed root, language, package manager,
   build/test commands and source owner.
2. Register feature, owner, metric, workload, query, collector, analyzer, policy
   and model-provider identities with pinned versions.
3. Approve ADRs for canonical serialization, object storage/KMS, policy boundary,
   worker sandbox, first language analyzers and model disclosure/fallback.
4. Define tenant quotas, source classifications, network destinations, command
   allowlist and maximum artifact/model/worker budgets.
5. Create runbook owners and dashboards before enabling a state-changing node.
6. Add representative fixtures: clean Git, dirty Git, unversioned source, raw text,
   structured request, missing workload, failing tests and incomparable evidence.

Exit: every dependency has an owner and version; no placeholder adapter is treated
as success; the pilot source can be snapshotted without writing into it.

## 5. Standard Node Build Procedure

Apply these steps independently to every node ID:

1. Confirm the node is present once in the canonical catalog and compiled subgraph.
2. Name exact input/output models and supported schema major versions.
3. Specify preconditions, postconditions, state fields written and allowed routes.
4. Classify the node as pure, read-only, idempotent write or external job.
5. Define idempotency key inputs, timeout, cancellation, retryable errors and
   unknown-outcome reconciliation.
6. Implement deterministic policy first; add model/tool behavior only behind ports.
7. Persist raw/large outputs, then return compact artifact references.
8. Emit audit event and telemetry with tenant-safe attributes.
9. Test success, each route, malformed input, policy denial, timeout, replay,
   restart and cross-tenant access where applicable.
10. Populate `NodeSpec`, runbook and SLO evidence; then change only that manifest
    entry from `disabled` to `ready/enabled`.

The handler never catches a terminal business error and converts it to success.
Transient retries reuse the same idempotency key. A process crash after an external
effect produces reconciliation or `unknown`, never an untracked retry.

## 6. A1 — Manual Requirement Contract

### A1 input and exit

- Input: authenticated `CreateManualCase`, raw text and/or structured payload,
  registered source reference and policy versions.
- Exit: immutable approved `OptimizationRequest@1.0` plus audit/handoff event.
- Blocking failures: invalid source, unresolved mandatory criterion/workload,
  policy denial, expired/mismatched approval.

### A1 implementation order by node

| Order/ID | Required implementation | Output and state write | Route/acceptance decision |
|---:|---|---|---|
| 1 — A1.10 | Authenticate command; validate tenant/case/thread/idempotency; limit payload; preserve original input as artifact | Intake envelope ref; intake mode | Reject empty, expired, oversized or cross-tenant input |
| 2 — A1.20 | Structured input uses schema path; raw text uses approved extractor; preserve unknowns and conflicts | Draft request ref; extraction confidence | Invalid model output gets one bounded repair; unresolved fields remain unknown |
| 3 — A1.30 | Resolve allowed root; canonicalize path; block traversal/symlink escape; detect Git, HEAD, dirty/untracked/submodules | Local source identity ref | `SOURCE_INVALID` on inaccessible or escaped source; do not equate dirty tree with HEAD |
| 4 — A1.40 | Bounded read-only discovery of languages, manifests, tools, tests, size, vendor/generated paths and service boundaries | Project profile ref; coverage | Unsupported parsing is an explicit coverage gap, not an empty success |
| 5 — A1.50 | Resolve registered feature first, then evidence-based paths/symbols; retain alternatives and confidence | Feature scope ref | Below threshold -> clarification; never invent feature identity |
| 6 — A1.60 | Separate objective from requester remedy; retain remedy only as hypothesis | Canonical objective | Missing or non-measurable outcome -> clarification |
| 7 — A1.61 | Define at least one criterion with metric, direction, target, unit and weight | Criterion set | Unknown unit/target/metric identity -> clarification |
| 8 — A1.62 | Define correctness plus relevant security, compatibility, cost/resource/quality guardrails | Guardrail set | No correctness guardrail -> hard failure |
| 9 — A1.63 | Apply tenant quota and policy; freeze deadline, worker/model/storage budgets and allowed analyzers | Execution budget | Requested budget over ceiling -> revise or reject; handler cannot self-expand budget |
| 10 — A1.70 | Resolve repository-owned workload/scenario, dataset, environment, warmups, repetitions, concurrency and cache | Workload contract | Missing material workload identity -> clarification |
| 11 — A1.71 | Map every criterion/guardrail to acceptable source types and minimum samples | Evidence requirement set and coverage matrix | A criterion without a feasible evidence path returns to A1.61/A1.70 |
| 12 — A1.80 | Deterministically check omissions, conflicts, units, duplicate IDs, impossible budget and overlapping scope; critic is advisory | A1 quality report | Pass -> A1.90; resolvable gaps -> clarification; impossible contract -> reject |
| 13 — A1.90 | Evaluate scope/data/risk/RBAC policy; create digest-bound interrupt where required; validate resume actor, role, expiry and policy | Approval decision/ref | Auto-accept, exact-thread interrupt or reject; digest change invalidates approval |
| 14 — A1.95 | Canonicalize, calculate digest, sign metadata, create-only persist and emit handoff event | OptimizationRequest ref; A1 complete | Publish only if schema, quality, policy and approval all match |

### A1 clarification payload

Every clarification interrupt must contain `interrupt_id`, case/thread, owning
node, immutable draft digest, exact missing/conflicting fields, allowed response
shape, required actor role, policy version and expiry. Resume re-enters the owning
node and creates a new draft version; it does not mutate the interrupted artifact.

### A1 exit tests

- Raw and structured inputs produce the same canonical request when semantically equal.
- Dirty/untracked content changes source identity.
- Path traversal, symlink escape and cross-tenant references fail closed.
- Missing criterion, correctness guardrail or workload cannot reach A1.95.
- Restart at both interrupts resumes the same thread and does not duplicate artifacts.
- Approval with wrong digest, actor, role, tenant, policy or expiry is rejected.

## 7. A2 — Shared Real Baseline in `active_collection`

### A2 input and exit

- Input: approved request ref and mode `active_collection`.
- Exit: `SourceSnapshot`, `RepositoryManifest`, `BaselineSnapshot`,
  `EvidenceBundle`, `EvidenceQualityReport` and `ComparabilityReport` refs.
- Non-negotiable: source remains unmodified; raw measurements are stored before
  normalization; missing mandatory evidence blocks A3.

### A2 implementation order by node

| Order/ID | Required implementation | Output and state write | Route/acceptance decision |
|---:|---|---|---|
| 1 — A2.10 | Verify request schema, digest, signature, approval, policy and source accessibility | A2 intake decision | Any identity mismatch returns to A1; no blind retry |
| 2 — A2.20 | Snapshot content, relevant modes, dirty/untracked files and submodule revisions; exclude secret/disallowed paths | SourceSnapshot ref | Source changing during capture -> retry from a clean new snapshot attempt |
| 3 — A2.30 | Parse snapshot to languages, modules, manifests, dependencies, symbols, tests, generated/vendor classification and tool coverage | RepositoryManifest ref | Partial parser support remains visible and is policy-evaluated |
| 4 — A2.31 | Resolve only repository-owned build/test/lint/type/security/benchmark argv and working directory | Verification manifest draft | Guessed shell strings or unresolved commands are ineligible |
| 5 — A2.40 | Bind every evidence requirement to collector/query/command, window, aggregation and minimum sample count | Collector plan ref | Missing configuration -> typed evidence interrupt; never silently drop a requirement |
| 6 — A2.41 | Pin tool/runtime/lockfile versions, env names, hardware, concurrency and cache state | Environment manifest ref | Secret values never enter state/artifact; only references are allowed |
| 7 — A2.50 | Authorize argv, write roots, egress, secret leases, resources and timeout; create worker jobs through broker | Execution authorization | Denied capability ends that branch; mandatory denial blocks the baseline |
| 8 — A2.60 | Run applicable static/dependency/security/complexity analyzers against snapshot | Static evidence refs | Findings are observations only; unsupported optional analyzer reports coverage |
| 9 — A2.61 | Run approved correctness checks; capture command, exit, duration, flakes and raw output | Test baseline refs | Missing tests are `unavailable`, not passing; baseline failure is retained as a guardrail fact |
| 10 — A2.62 | Run exact warmups/repetitions; preserve each sample and environment identity | Metric sample refs | Hard timeout/cancel via worker; partial samples follow registered policy |
| 11 — A2.63 | Import declared logs/metrics/traces/profiles/LLM traces using bounded queries | Telemetry evidence refs | Missing commit/feature dimensions lower trust and may make samples ineligible |
| 12 — A2.64 | Build file/symbol/call/service/runtime map with confidence and unresolved edges | Source map/code graph refs | Dynamic gaps remain unknown; they are never filled by narrative |
| 13 — A2.70 | Fan-in by stable branch identity; write raw bytes create-only with digest, MIME, collector/tool/command/time metadata | Ordered raw artifact refs | Conflicting digest for one identity fails fan-in; completion order has no effect |
| 14 — A2.71 | Normalize registered units/dimensions and aggregate only after raw preservation | Normalized evidence refs | Unknown conversion/schema -> invalid evidence; raw value remains intact |
| 15 — A2.80 | Bind tenant/case/request/feature/snapshot/workload/dataset/environment/model/sample/trace/collector provenance and assign trust | EvidenceBundle ref | Missing material identity caps trust and prevents T3 |
| 16 — A2.90 | Evaluate coverage, samples, freshness, integrity, redaction, failures and analyzer coverage | EvidenceQualityReport ref | Mandatory failure -> recollect/interrupt/terminate; optional gap proceeds only by policy |
| 17 — A2.91 | Compare all material dimensions and produce per-dimension verdict | ComparabilityReport ref | Any material mismatch -> `incomparable`, return to exact collector/workload owner |
| 18 — A2.95 | Aggregate from eligible raw samples; bind all parent digests; sign and publish A3 handoff | BaselineSnapshot and final refs | Complete only when quality and comparability are true |

### A2 fan-out rules

- Branch IDs are fixed: `A2.60`–`A2.64`.
- Each branch owns a separate intent and artifact set.
- Reducer key is `(branch_kind, branch_id, idempotency_key)`.
- Fan-in sorts by branch ID and refuses two digests for the same key.
- Optional branch failure stays in the quality report; it cannot be overwritten.
- Cancellation waits for/reconciles all accepted worker jobs before closing the case.

### A2 exit tests

- Snapshot can reproduce clean, dirty and unversioned pilot sources byte-for-byte.
- Worker cannot escape write roots, use undeclared egress or receive raw secrets.
- Crash before/after collector effect reconciles without duplicate execution.
- Collector partial failure remains visible; mandatory failure cannot reach A2.95.
- Aggregate values trace to raw sample IDs and versioned transformations.
- Environment, dataset, cache or source mismatch produces `comparable=false`.

## 8. A3 — Shared Grounded Analysis in `manual` Origin

### A3 input and exit

- Input: request, snapshot, manifest, baseline, evidence and passed reports.
- Exit: `FindingSet`, `SolutionPortfolio` and `A3QualityReport` refs.
- Non-negotiable: A3 reads source/evidence but does not edit source or select a
  final strategy.

### A3 implementation order by node

| Order/ID | Required implementation | Output and state write | Route/acceptance decision |
|---:|---|---|---|
| 1 — A3.10 | Verify complete A1/A2 schema, digest chain, signatures, snapshot and gate status | A3 intake decision | Invalid/weak handoff returns to exact A1/A2 producer |
| 2 — A3.11 | Index evidence IDs, trust, metrics, windows, feature locations and counterevidence | Evidence catalogue ref | Index is lookup authority, not causal proof |
| 3 — A3.20 | Deterministically compare baseline with criteria/guardrails and distributions | ProblemSignal refs | No measured problem -> close `NO_ACTIONABLE_PROBLEM` |
| 4 — A3.21 | Score impact, weight, scope, frequency, severity and trust using versioned policy | Signal priority ref | Retain nonselected signals; score cannot override hard trust gates |
| 5 — A3.30 | Syntax/pattern analysis near prioritized signals | Analyzer observation refs | Positive locations plus coverage; pattern match is not cause |
| 6 — A3.31 | Applicable semantic/data-flow/call-graph analysis | Semantic observation refs | Unsupported language emits an explicit gap |
| 7 — A3.32 | Applicable domain analysis only | Domain observation refs | Do not run tools merely to populate a category |
| 8 — A3.33 | Correlate traces/profiles/logs/samples to files, symbols and paths | Runtime correlation refs | Preserve correlation/causation distinction |
| 9 — A3.40 | Give approved generator bounded catalogue context; require typed claims, evidence IDs, counterevidence and unknowns | Finding draft refs | Untrusted T0 output; one schema repair within budget |
| 10 — A3.41 | Resolve every citation, scope and exact claim support deterministically | Citation resolution refs | Missing, negative-only or irrelevant citation rejects the claim |
| 11 — A3.50 | Independent judge checks symptom, location, wording and contradictions | Finding judgement refs | Generator cannot judge itself; model verdict cannot bypass deterministic failure |
| 12 — A3.51 | Assign observation/hypothesis/verified-cause maturity | FindingSet ref | Verified cause requires measured symptom, positive corroboration, T4 and accepted judgement |
| 13 — A3.60 | Generate materially different strategies tied to eligible findings | Strategy draft refs | Hypothesis-only work may create diagnostic phases only |
| 14 — A3.61 | Define ordered phases with exactly one logical treatment each | Phase template refs | Multi-file change is allowed only when it is one inseparable treatment |
| 15 — A3.62 | Assess every criterion and guardrail; label measured versus forecast | Impact assessment refs | Omitted dimension is a hard portfolio failure |
| 16 — A3.63 | Capture concrete pros, cons, prerequisites, effort, uncertainty, operations and opportunity cost | Tradeoff refs | Generic prose or unsupported certainty fails |
| 17 — A3.64 | Bind validation commands/protocol, expected movement, stop conditions and executable rollback | Validation/rollback refs | Invented command or unresolved rollback makes strategy ineligible |
| 18 — A3.70 | Resolve every path/symbol/config/prompt/dependency against snapshot or label proposed creation | Scope resolution refs | Unaccepted unresolved scope is ineligible |
| 19 — A3.80 | Determine risk from blast radius, reversibility, uncertainty, migration and security | Risk assessment refs | Risk tier is policy-owned, not model-owned |
| 20 — A3.81 | Apply evidence, maturity, diversity, coverage, treatment and rollback hard gates | A3QualityReport ref | At least policy-required materially different eligible strategies |
| 21 — A3.82 | Revise only failed dimensions; preserve attempts; enforce retry/token deadline | Revised candidate refs | Budget exhausted -> visible `NO_ELIGIBLE_SOLUTION` |
| 22 — A3.90 | Seal findings, all eligible/ineligible strategies, quality and provenance | FindingSet/SolutionPortfolio refs | Handoff is proposal-ready, not implementation approval |

### A3 fan-out and model rules

- Analyzer fan-out uses stable IDs `A3.30`–`A3.33` and deterministic fan-in.
- Model context lists included/omitted evidence and source regions, byte/token limit,
  redaction profile and disclosure classification.
- Generator and judge use separate configured identities when policy requires it.
- Prompt, model, tool and policy versions participate in the idempotency key.
- Provider fallback is allowlisted and preserves every attempt and cost reference.
- A3.82 may target only rejected findings/strategies; accepted artifacts are reused.

### A3 exit tests

- A threshold breach cannot become a causal claim without corroboration.
- A nonexistent or negative-only citation cannot produce `verified_cause`.
- Hypothesis-only strategies contain diagnostic phases only.
- Duplicate strategies and generic tradeoffs fail A3.81.
- Every eligible strategy covers all criteria/guardrails and resolves rollback.
- Model/provider timeout, invalid JSON and budget exhaustion terminate predictably.

## 9. C0 — Lane 1 Convergence Slice

| Order/ID | Required implementation | Pass condition | Failure owner |
|---:|---|---|---|
| 1 — C0.10 | Pin immutable origin | Origin equals `manual` from case creation | Root/case creation |
| 2 — C0.20 | Validate supported schemas and migration readers | All required artifacts validate | Exact artifact producer |
| 3 — C0.30 | Verify content and parent digest chain | One request/snapshot/evidence/portfolio lineage | Exact artifact producer |
| 4 — C0.40 | Apply common semantic-equivalence rules | Manual package satisfies canonical A1/A2/A3 contract | A1, A2 or A3 by failed dimension |
| 5 — C0.50 | Recheck source, evidence, approval and ownership freshness | All freshness policies pass | Refresh owning stage or version case |
| 6 — C0.60 | Require comparable baseline, passed A3 quality and eligible strategy | Deterministic convergence decision is true | A2 or A3 |
| 7 — C0.70 | Create-only publish lane-neutral envelope and event | `ConvergedCase@1.0` persisted once | Control plane |

C0 has no blind retry for schema, digest, equivalence or freshness errors. It
returns a typed reason and producer node; the graph alone selects the return edge.

## 10. State and Artifact Write Matrix

| Stage | May write compact state | Must write artifact store | Must not write |
|---|---|---|---|
| A1 | Request/draft/interrupt refs, decisions, routes | Original input, drafts, quality and request | Source content in checkpoint |
| A2 | Snapshot/manifest/evidence/report refs, branch status | Source snapshot, raw outputs, samples, manifests, reports | Raw telemetry or command output in checkpoint |
| A3 | Signal/finding/strategy/quality refs, revision counters | Analyzer/model outputs, findings, portfolio, provenance | Prompt/source excerpts in telemetry |
| C0 | Decision/converged refs and route | Decision, envelope and event payload | Rewritten upstream artifacts |

Identity fields `tenant_id`, `case_id`, `thread_id`, `entrypoint`, `lane` and
origin are initialized once and protected from handler mutation.

## 11. Error, Retry and Return Matrix

| Condition | Retry | Route |
|---|---|---|
| Invalid input/policy/digest/schema | No | Owning clarification, rejection or producer |
| Source changed | No replay against old identity | New snapshot/request version |
| Transient database/object-store/network | Bounded with same key | Same node; dead-letter at limit |
| Worker timeout/loss | Cancel then reconcile | Same node or typed unavailable route |
| Unknown external effect | Never blind retry | Intent `unknown`, reconcile/operator route |
| Model invalid output | One repair and approved fallback | Fail visibly at budget limit |
| Missing mandatory evidence/incomparability | No blind retry | A2.40/A2.90/A2.91 |
| Unsupported/weak finding or strategy | Targeted bounded revision | A3.40/A3.60 through A3.82 |
| Expired approval | No | Re-enter owning approval node |

## 12. Lane 1 Release Gates

| Gate | Required evidence |
|---|---|
| L1-G1 A1 | All 14 nodes ready; clarification/approval restart tests; immutable request |
| L1-G2 A2 foundation | Exact snapshot and manifest reconstruction; safe commands only |
| L1-G3 A2 evidence | Mandatory coverage, provenance and comparability; collector replay proof |
| L1-G4 A3 deterministic | Signals, citations, maturity and portfolio gates reject adversarial fixtures |
| L1-G5 A3 model | Disclosure, budget, fallback and independent-judge tests pass |
| L1-G6 C0 | Fresh digest-valid manual case publishes once |
| L1-G7 production | Tenant isolation, sandbox, load/SLO, backup/restore, failure drill and runbooks pass |

Promotion order is unit/contract, local integration, suggest-only pilot, controlled
tenant canary, then Lane 1 production. No business handler is enabled globally;
enablement is environment-, tenant- and capability-scoped.

## 13. Lane 1 Definition of Done

Lane 1 is done only when a real pilot request can resume across process failures
and produce one `ConvergedCase` from real source and measurements; every artifact
is reconstructable from immutable references; every mandatory failure stops at
the correct owner; no duplicate side effect occurs; no source file is modified;
and operators can inspect, cancel, reconcile and audit the case without direct
database or object-store mutation.

