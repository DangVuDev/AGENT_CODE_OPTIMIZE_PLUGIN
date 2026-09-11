# Lane B LangGraph and Operating Model

## Orchestration Decision

Lane B is a subgraph of the same root LangGraph used by Lane A. An external
scheduler or telemetry event may trigger a run, but no standalone cron script,
collector, LLM or analyzer owns business progression.

```text
OptimizationRootGraph
|-- initialize_case
|-- route_origin
|   |-- Lane A: A1 -> A2 -> A3
|   `-- Lane B
|       |-- B1DiscoverySubgraph
|       |   |-- registry -> historical collectors -> normalization
|       |   |-- detection -> binding -> scoring -> dedup
|       |   `-- reconstruct_A1 -> reuse_A2 -> qualify -> case-start outbox
|       `-- new case/thread -> B2ProposalSubgraph
|           |-- strategy -> A3Subgraph -> discovery validation
|           `-- routing -> interrupt/revision -> seal
`-- C0 -> 01 Re-rank or user decision
```

## Why B1 and B2 Must Remain Separate

| Concern | B1 | B2 |
|---|---|---|
| Primary question | Is there a real, actionable opportunity? | What verified solutions should be proposed? |
| Main data | Historical observations and registries | Qualified evidence, local source and analyzer outputs |
| Typical cost | Many cheap scans | Fewer expensive analyzers and LLM calls |
| Failure outcome | Suppress, quarantine or no-op | Revise, request evidence or no eligible solution |
| Side-effect boundary | Read historical data and snapshot source | Read-only analysis and proposal publication |

Combining them would spend LLM/analyzer budget on noise and make discovery
failures indistinguishable from proposal-quality failures.

They also use different durable identities. B1 is a scan thread that may produce
zero or many opportunities. Each qualified opportunity is sealed and delivered
through a transactional outbox to one new B2 case/thread. The dispatcher invokes
the same root graph and cannot select its route. This keeps case budgets,
approvals, cancellation and artifact chains isolated while making delivery
idempotent under DEC-ARCH-003.

## Lane B State

```python
class DiscoveryScanState(TypedDict, total=False):
    thread_id: str
    scan_id: str
    tenant_id: str
    observation_window: TimeWindow
    policy_versions: dict[str, str]
    registered_source_refs: list[ArtifactRef]
    historical_inventory_ref: ArtifactRef
    run_group_refs: Annotated[list[ArtifactRef], merge_unique_refs]
    signal_refs: Annotated[list[ArtifactRef], merge_unique_refs]
    opportunity_refs: Annotated[list[ArtifactRef], merge_unique_refs]
    suppression_refs: Annotated[list[ArtifactRef], merge_unique_refs]
    case_start_event_refs: Annotated[list[ArtifactRef], merge_unique_refs]
    candidate_cursor_ref: ArtifactRef
    pending_interrupt: InterruptEnvelope | None
    error_refs: Annotated[list[ArtifactRef], merge_unique_refs]
    budget_usage_ref: ArtifactRef

class LaneBCaseState(TypedDict, total=False):
    thread_id: str
    case_id: str
    tenant_id: str
    origin: Literal["automatic"]
    qualified_opportunity_ref: ArtifactRef
    request_ref: ArtifactRef
    baseline_ref: ArtifactRef
    evidence_bundle_ref: ArtifactRef
    finding_set_ref: ArtifactRef
    solution_portfolio_ref: ArtifactRef
    quality_report_ref: ArtifactRef
    proposal_ref: ArtifactRef
    pending_interrupt: InterruptEnvelope | None
    error_refs: Annotated[list[ArtifactRef], merge_unique_refs]
    budget_usage_ref: ArtifactRef
```

Large query results, source snapshots and model transcripts remain in artifact
storage; checkpoints contain references only [SRC-LG-PERSIST].

## Fan-Out and Fan-In Rules

LangGraph may fan out historical collectors, signal detectors, local
repositories, analyzers and risk-tier solution generators. Every fan-in node
must define:

- stable deduplication key;
- deterministic reducer;
- mandatory versus optional branch policy;
- maximum parallelism and budget;
- deadline and transient retry policy;
- partial-failure artifact;
- ordering independent of completion time.

The first collector or model response to finish never wins by default.

## Interrupt Points

| Interrupt | Trigger | Required response |
|---|---|---|
| `B1_SOURCE_MAPPING` | Signal cannot bind to one local source revision | Select source/revision or reject candidate |
| `B1_FEATURE_MAPPING` | Multiple features remain plausible | Select feature/scope with evidence |
| `B1_OWNER_CONFIRMATION` | Ownership is missing or conflicting | Assign accountable owner |
| `B1_EVIDENCE_GAP` | Strong signal lacks comparable baseline | Provide approved evidence route or close |
| `B2_PROPOSAL_REVIEW` | Policy requires owner/security review | Approve, revise or reject digest-bound proposal |

An interrupt payload contains no secrets or raw source. Resume uses the same
owning scan or case `thread_id`, authenticated actor and artifact digest
[SRC-LG-INTERRUPT]. A B1 interrupt never resumes a B2 case, and vice versa.

## Case Status Model

```text
SCAN_CREATED
  -> COLLECTING_HISTORY
  -> DETECTING
  -> QUALIFYING
  -> NO_OP | SUPPRESSED | QUARANTINED | CASE_START_PUBLISHED
  -> SCAN_COMPLETE

CASE_START_PUBLISHED
  -> B2_CASE_CREATED
  -> B2_ANALYZING
  -> EVIDENCE_REQUIRED | NO_ELIGIBLE_SOLUTION | REVIEW_REQUIRED
  -> B2_APPROVED | B2_REJECTED
  -> CONVERGENCE_READY
```

Every terminal status is a valid audited business outcome. `NO_OP` does not
mean the system failed.

## Traceability to Lane A

| Lane B artifact/task | Lane A equivalent | Reuse requirement |
|---|---|---|
| B1 reconstructed request | A1 `OptimizationRequest@1.0` | Same schema and validation rules; origin differs |
| B1 recovered baseline | A2 canonical baseline/evidence/report artifacts | Same provenance, coverage and comparability gates |
| B2 grounded analysis | A3 `FindingSet@1.0`, `SolutionPortfolio@1.0`, and quality report | Invoke the same subgraph implementation |
| B2 approval/routing | A3 handoff plus automatic owner routing | Additional controls may be stricter, never weaker |
| Lane B convergence package | C0 input | Same digest binding and at least one eligible solution |

## Operational Metrics

| Category | Metrics |
|---|---|
| Discovery effectiveness | Qualified opportunities, confirmed precision, false-positive rate, missed-known-incident rate |
| Noise control | Deduplication rate, cooldown suppressions, reopened-case rate |
| Evidence quality | Source-binding rate, comparable-baseline rate, provenance failures |
| Proposal quality | Verified-cause rate, eligible-solution rate, owner approval/revision/rejection rate |
| Reliability | Scan success, collector failures, resume success, stale-case cancellation |
| Cost | Query volume, analyzer minutes, model tokens/cost per qualified proposal |
| Latency | Detection lag, B1 qualification time, B2 proposal time, owner-response time |

## Production Acceptance Criteria

Lane B is production-ready only when:

1. A scheduled scan and every candidate branch survive process restart.
2. Repeated triggers do not duplicate scans, cases or external queries.
3. Source/feature/owner ambiguity never silently resolves itself.
4. No-data scans finish successfully without creating proposals.
5. Historical observations can be traced to raw artifacts, query versions and
   source/workload dimensions.
6. B1 output validates against the same A1/A2 contracts used by Lane A.
7. B2 invokes the same A3 implementation and deterministic quality gates.
8. LLM failure, invalid JSON or fabricated citation cannot pass proposal gates.
9. Deduplication and cooldown behavior are replayable from audit artifacts.
10. Read credentials are short-lived and never persisted in graph state,
    prompts or logs.
11. Query/analyzer/model budgets and timeouts are enforced per tenant.
12. Every convergence package is digest-bound and accepted by C0 before step
    01 begins.

## Delivery Backlog

| Epic | Deliverables |
|---|---|
| EPIC-B1-REGISTRY | Local repository, feature, metric, owner, query and policy registries |
| EPIC-B1-COLLECT | Read-only historical adapters, raw artifact preservation and dimension normalization |
| EPIC-B1-DETECT | Threshold, regression, hotspot, reliability and LLM-quality detectors |
| EPIC-B1-QUALIFY | Source/feature binding, scoring, deduplication, cooldown and qualification gates |
| EPIC-B1-EQUIVALENCE | Automatic A1 contract construction and A2 baseline recovery through shared validators |
| EPIC-B2-ANALYZE | Strategy routing and direct reuse of the A3 grounded-analysis subgraph |
| EPIC-B2-ROUTE | Proposal envelope, staleness check, owner routing, interrupt and revision |
| EPIC-B-CROSS | Durable checkpoints, idempotency, secrets broker, policy engine, audit, cost and operational telemetry |
