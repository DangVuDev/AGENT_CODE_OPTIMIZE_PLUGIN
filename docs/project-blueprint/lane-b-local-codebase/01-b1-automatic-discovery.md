# B1. Automatic Bottleneck Discovery and Baseline Recovery

## Business Objective

B1 continuously or periodically examines real historical observations, detects
material regressions or inefficiencies, associates them with a registered local
codebase and feature, and creates a qualified optimization case without asking
a user to define the problem first.

B1 answers:

1. Is there a measurable problem or opportunity?
2. Does it belong to a known feature and exact local source state?
3. Is it important enough and sufficiently supported to open a case?
4. Is there already an active or recently closed equivalent case?
5. Can the available evidence form a trustworthy baseline?

## Actors

| Actor | Responsibility |
|---|---|
| Scheduler/event adapter | Starts a scan for a configured observation window |
| LangGraph control plane | Owns scan state, fan-out, gates, retry, interrupt and audit |
| Discovery policy owner | Defines thresholds, scoring, cooldown, budgets and eligible repositories |
| Repository owner | Owns the local codebase and receives qualified proposals |
| Observability adapters | Read existing measurements without modifying the application |
| Human reviewer | Resolves ownership, source mapping or high-risk policy exceptions |

## Preconditions

- At least one local repository is registered with stable `repository_id`, path,
  owner, allowed scope and source identity policy.
- At least one historical evidence source is registered and queryable.
- Feature, metric, ownership, deduplication and discovery policies are versioned.
- Observation data contains enough dimensions to attempt feature and source
  correlation. Missing dimensions are not inferred as facts.

## B1 Subgraph

```mermaid
flowchart TD
    B110[B1.10 Start scan] --> B120[B1.20 Load registered local sources]
    B120 --> B130[B1.30 Inventory historical evidence]
    B130 --> P{Parallel signal queries}
    P --> M[Metrics and SLOs]
    P --> L[Logs and failures]
    P --> T[Traces and profiles]
    P --> G[LLM quality/token traces]
    M --> B140[B1.40 Normalize and group runs]
    L --> B140
    T --> B140
    G --> B140
    B140 --> B150[B1.50 Detect opportunities]
    B150 --> B160[B1.60 Correlate feature and source]
    B160 -->|unresolved| Q[Quarantine/interrupt]
    B160 --> B170[B1.70 Score and prioritize]
    B170 --> B180[B1.80 Deduplicate and cooldown]
    B180 --> B190[B1.90 Reconstruct A1 contract]
    B190 --> B195[B1.95 Recover and validate A2 baseline]
    B195 -->|insufficient| H[Evidence request or close]
    B195 -->|pass| O[QualifiedOpportunity@1.0]
```

## Task Catalogue

| ID | Business task | Processing and rules | Inputs | Outputs | Framework fit | Gap and complement |
|---|---|---|---|---|---|---|
| B1.10 | Initialize discovery scan | Create scan/thread IDs, freeze observation window and policy versions, enforce tenant and budget limits | Trigger, scan policy | `DiscoveryScanContext` | LangGraph checkpoints [SRC-LG-OVERVIEW] [SRC-LG-PERSIST] | LangGraph does not schedule by itself; an external timer/event starts the graph, while LangGraph owns all subsequent work |
| B1.20 | Load source registry | Resolve approved local paths, owners, repository identities, feature mappings and exclusions; quarantine inaccessible or changed registrations | Repository registry | `RegisteredSourceSet` | Git CLI [SRC-GIT], Pydantic [SRC-PYDANTIC] | Registry data can become stale; verify canonical path and current source identity on every scan |
| B1.21 | Fingerprint current local source | Record Git revision, dirty status and bounded content identity so observations can be correlated without silently assuming HEAD represents the working tree | Registered source | `ObservedSourceIdentity` | Git [SRC-GIT] | Dirty/untracked content needs content hashing; full immutable snapshot is required before proposal analysis |
| B1.30 | Inventory historical sources | Determine available metric, log, trace, profile, benchmark and LLM-evaluation windows; do not execute new workloads | Scan context and collector registry | `HistoricalSourceInventory` | Prometheus, Loki, Tempo, Pyroscope, MLflow [SRC-PROM] [SRC-LOKI] [SRC-TEMPO] [SRC-PYROSCOPE] [SRC-MLFLOW] | Availability does not mean usability; query results still require provenance and dimension checks |
| B1.31 | Authorize read queries | Resolve credentials by reference, approve endpoints, enforce query time/range/cardinality limits and redact protected fields | Collector plan and policy | `ReadAuthorization` | OPA [SRC-OPA] | Policy cannot secure endpoints by itself; use secret manager, network controls and read-only credentials |
| B1.32 | Query existing metrics | Read SLO, latency, throughput, error, cost, resource or domain metrics for the frozen window | Metric registry and authorization | `HistoricalMetricSamples[]` | Prometheus [SRC-PROM] | Metrics show magnitude and trend but often lack source location; correlate with traces/profiles and deployment identity |
| B1.33 | Query existing logs | Extract structured failures, timeouts and feature events with stable labels and trace IDs | Log query registry | `HistoricalLogEvents[]` | Loki [SRC-LOKI] | Free-text logs are noisy and may expose secrets; require structured queries, redaction and corroboration |
| B1.34 | Query traces and profiles | Read slow paths, spans, call stacks, CPU and memory hotspots that already exist | Trace/profile registry | `HistoricalRuntimeEvidence[]` | Tempo, Pyroscope and OpenTelemetry [SRC-TEMPO] [SRC-PYROSCOPE] [SRC-OTEL] | Instrumentation gaps create biased samples; coverage must be measured and reported |
| B1.35 | Query LLM execution evidence | Read historical model, prompt, token, cost, latency and evaluation records when the feature uses LLMs | LLM trace registry | `HistoricalLLMEvidence[]` | MLflow tracing/evaluation [SRC-MLFLOW] [SRC-MLFLOW-EVAL] | Automated scorers may be weak and hosted traces cost storage; require versioned datasets/scorers and policy |
| B1.40 | Normalize and group real runs | Preserve raw records, normalize units, then group by feature, source/deployment, workload, dataset, environment, model and material dimensions | Historical evidence | `HistoricalRunGroups` | Pydantic/JSON Schema [SRC-PYDANTIC] [SRC-JSON-SCHEMA] | Missing grouping keys cannot be backfilled by an LLM; downgrade trust or exclude records |
| B1.41 | Validate observation eligibility | Exclude synthetic templates, stale records, untrusted sources, incompatible schemas and runs below minimum sample requirements | Run groups and trust policy | `EligibleRunSet`, exclusion report | Deterministic policy/OPA [SRC-OPA] | Threshold quality is business-owned; calibrate against known incidents and false positives |
| B1.50 | Detect threshold breaches | Identify breached SLOs, guardrails or configured budget limits using deterministic comparisons | Eligible runs and metric policy | `ThresholdSignal[]` | Prometheus rules [SRC-PROM], k6-style thresholds where imported [SRC-K6] | Thresholds miss unknown degradation and may be poorly configured; complement with trend/anomaly detection |
| B1.51 | Detect regressions and trends | Compare compatible historical windows, detect material directional change and quantify effect size | Comparable run groups | `RegressionSignal[]` | Bencher concepts for benchmark history [SRC-BENCHER] | Statistical change may be seasonal or workload-driven; preserve dimensions and require practical significance |
| B1.52 | Detect hotspots and recurrent failures | Aggregate repeated profile frames, slow spans, error signatures and resource saturation by feature/path | Runtime evidence | `HotspotSignal[]` | Pyroscope, Tempo, Loki [SRC-PYROSCOPE] [SRC-TEMPO] [SRC-LOKI] | Frequency is not causality; B2 must verify source and root cause |
| B1.53 | Detect quality/token opportunities | Find quality drift, token inflation, latency/cost increase or scorer failures in LLM-backed features | LLM evidence | `LLMOpportunitySignal[]` | MLflow evaluation [SRC-MLFLOW-EVAL] | Scorer drift can mimic product drift; verify dataset and scorer versions |
| B1.60 | Resolve feature identity | Map signals to a registered `feature_id` using explicit labels first, then trace/service/symbol mappings; preserve confidence and alternatives | Signals, feature registry and source maps | `FeatureBinding` | OTel semantic correlation [SRC-OTEL], Tree-sitter [SRC-TREE] | Heuristic source matching is not authoritative; ambiguous mappings require quarantine or human resolution |
| B1.61 | Bind source snapshot | Resolve deployment/trace commit when available and match it to local Git objects; otherwise mark source identity unresolved rather than using current HEAD | Feature binding and Git repository | `SourceBinding` | Git [SRC-GIT] | Historical deployment code may no longer exist locally; fetch/import is outside local-only scope, so quarantine the case |
| B1.62 | Identify owner | Resolve code owner, service owner and decision owner from registry; detect conflicts and unavailable owners | Feature/source binding | `OwnershipBinding` | Platform registry and policy | Git metadata does not establish accountable ownership; maintain explicit ownership data and escalation policy |
| B1.70 | Score opportunity | Score severity, frequency, user/business impact, criterion breach, evidence trust, estimated addressability and strategic priority | Bound signals and policy | `OpportunityScore` | Deterministic scoring plus OPA hard gates [SRC-OPA] | Weights can bias discovery; version them and monitor selection fairness/precision |
| B1.71 | Apply qualification gate | Require minimum trust, source/feature/owner resolution, actionability, sufficient samples and policy eligibility | Opportunity score and bindings | `QualificationDecision` | OPA [SRC-OPA] | A high score cannot override missing hard evidence or ownership |
| B1.80 | Deduplicate opportunities | Compare feature, metric, time window, signal and source fingerprints against active/recent cases; merge evidence without merging unrelated causes | Qualified candidates and case index | New, merged or suppressed decision | Platform-owned fingerprint/index | Semantic similarity alone can merge distinct incidents; use deterministic keys plus bounded LLM assistance only for review |
| B1.81 | Enforce cooldown and noise controls | Suppress repeated alerts until material change, expiry or prior-case decision; record every suppression | Candidate and history | `CooldownDecision` | OPA [SRC-OPA] | Excessive cooldown hides renewed regressions; allow severity-based override with audit |
| B1.90 | Reconstruct A1-equivalent request | Derive objective, primary criteria, guardrails, priority, scope, workload identity and evidence requirements from verified observations and registries | Qualified opportunity | `OptimizationRequest@1.0` with origin `automatic` | Pydantic [SRC-PYDANTIC], LangGraph | Lane B has no requester to fill gaps; unresolved mandatory fields block or interrupt instead of being guessed |
| B1.91 | Evaluate automatic-intake policy | Decide whether the reconstructed contract may be accepted automatically or needs owner confirmation | Request, risk and policy | Approval or `interrupt()` payload | OPA and LangGraph interrupt [SRC-OPA] [SRC-LG-INTERRUPT] | Interrupt requires authenticated resume and digest-bound approval |
| B1.95 | Recover A2 baseline | Convert eligible historical runs into A2 evidence contracts, preserve raw artifacts and run the same provenance, coverage and comparability gates as Lane A | Approved request and run groups | `BaselineSnapshot@1.0`, `EvidenceBundle@1.0`, and reports | Reuse A2 subgraph and evidence frameworks | Reusing data does not waive A2 rules; insufficient history blocks B2 or creates a targeted evidence request |
| B1.96 | Seal qualified opportunity | Bind scan, signal, request, baseline, ownership, policy and suppression metadata; atomically write an idempotent case-start outbox record for each qualified opportunity | Passed artifacts | `QualifiedOpportunity@1.0`, `CaseStartRequested` | LangGraph persistence, PostgreSQL outbox and immutable storage [SRC-LG-PERSIST] [SRC-MINIO-LOCK] | Sealing does not prove cause; a dispatcher invokes the same root graph at B2.10 and cannot choose workflow routes |

## Detection Policy

Discovery should combine deterministic methods rather than depend on one
generic anomaly score:

| Detection class | Required evidence | Qualification example |
|---|---|---|
| Absolute breach | Metric exceeds a registered limit | p95 latency exceeds SLO for three eligible windows |
| Relative regression | Comparable current and reference windows | p95 increases by at least policy delta with practical significance |
| Cost/token inflation | Usage and output-quality identity | Cost rises while quality does not improve |
| Runtime hotspot | Recurrent trace/profile concentration | One symbol consumes a material share across enough feature-tagged runs |
| Reliability defect | Repeated structured failures | Same fingerprint breaches frequency/severity threshold |
| Quality drift | Versioned dataset/scorer records | Quality falls below guardrail with unchanged scorer and dataset cohort |

## Core Business Rules

| Rule | Requirement |
|---|---|
| BR-B1-001 | Discovery reads existing real observations and does not launch a special measurement workload |
| BR-B1-002 | Every scan has a frozen time window, policy version and collector-query identity |
| BR-B1-003 | A signal without feature, source and owner binding cannot become a qualified case |
| BR-B1-004 | Current local HEAD cannot substitute for the historical source revision |
| BR-B1-005 | Threshold breach and anomaly detection establish a symptom, not a root cause |
| BR-B1-006 | Missing material dimensions make affected samples ineligible for comparison |
| BR-B1-007 | Deduplication must preserve newly observed evidence and suppression reasons |
| BR-B1-008 | Lane B must reconstruct all mandatory A1 fields or stop for owner input |
| BR-B1-009 | The recovered baseline passes the same A2 quality and comparability policy as Lane A |
| BR-B1-010 | No opportunity is opened solely from LLM narrative or static source scanning |
| BR-B1-011 | A scan with no qualified opportunities is a successful no-op, not a workflow failure |
| BR-B1-012 | Discovery cost, frequency and query cardinality remain within tenant budgets |

## Exception Routes

| Condition | LangGraph route |
|---|---|
| Observability endpoint temporarily unavailable | Retry with backoff inside scan deadline; then mark source partial |
| No eligible historical evidence | Complete scan as `NO_DATA`; do not manufacture a case |
| Historical source revision absent locally | Quarantine as `SOURCE_UNRESOLVED` |
| Feature mapping ambiguous | Interrupt owner with ranked candidates and evidence |
| Duplicate active case | Merge new evidence and end this candidate branch |
| Candidate in cooldown | Record suppression and continue scanning |
| Strong signal but incomplete baseline | Emit targeted evidence request; do not enter B2 |

## Definition of Done

B1 is complete when the scan report is sealed and every detected candidate is
either qualified, merged, suppressed, quarantined or rejected with a reason. A
qualified candidate includes a complete A1-equivalent request, real A2 baseline,
resolved local source and owner, passed quality/comparability gates, and a full
audit trail from historical observation to opportunity.
