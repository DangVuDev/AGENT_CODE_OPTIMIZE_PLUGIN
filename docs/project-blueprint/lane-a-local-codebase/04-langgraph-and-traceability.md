# LangGraph Orchestration and A1-A3 Traceability

## Control-Plane Decision

One root LangGraph `StateGraph` owns A1, A2 and A3. Each stage is a compiled
subgraph. Every task ID in the three task catalogues—54 nodes from A1.10 through
A3.90—is registered as a separate LangGraph node, including adjacent pure tasks
that happen to share retry characteristics. This stricter rule prevents later
implementations from hiding catalogued controls inside a stage service.
Collectors and analyzers run behind typed adapters; they never mutate workflow
state directly or choose a route [SRC-LG-OVERVIEW].

```text
LocalOptimizationRootGraph
|-- initialize_case
|-- A1RequirementSubgraph
|   |-- A1.10 -> A1.20 -> A1.30 -> A1.40 -> A1.50
|   |-- A1.60 -> A1.61 -> A1.62 -> A1.63 -> A1.70 -> A1.71
|   `-- A1.80 -> interrupt/A1.90 -> interrupt/A1.95
|-- A2BaselineSubgraph
|   |-- A2.10 -> A2.20 -> A2.30 -> A2.31 -> A2.40 -> A2.41 -> A2.50
|   |-- fan_out(A2.60, A2.61, A2.62, A2.63, A2.64)
|   `-- A2.70 -> A2.71 -> A2.80 -> A2.90 -> A2.91 -> A2.95
|-- A3SolutionSubgraph
|   |-- A3.10 -> A3.11 -> A3.20 -> A3.21
|   |-- fan_out(A3.30, A3.31, A3.32, A3.33)
|   |-- A3.40 -> A3.41 -> A3.50 -> A3.51 -> A3.60
|   |-- A3.61 -> A3.62 -> A3.63 -> A3.64 -> A3.70
|   `-- A3.80 -> A3.81 -> A3.82 revision route / A3.90
`-- C0LaneConvergence
```

The diagram is an inventory summary. Conditional edges, fan-in reducers,
interrupt payloads, targeted A3 revision routes, node runtime protocol, and the
node-level framework matrix are normative in
[`../../implementation/lane-1-langgraph-architecture.md`](../../implementation/lane-1-langgraph-architecture.md).

## State Ownership

Graph state contains references and decisions, not source files or raw logs:

```python
class LocalOptimizationState(TypedDict, total=False):
    thread_id: str
    case_id: str
    status: str
    current_task: str
    request_ref: ArtifactRef
    source_snapshot_ref: ArtifactRef
    repository_manifest_ref: ArtifactRef
    collector_plan_ref: ArtifactRef
    baseline_ref: ArtifactRef
    evidence_bundle_ref: ArtifactRef
    finding_set_ref: ArtifactRef
    solution_portfolio_ref: ArtifactRef
    quality_report_ref: ArtifactRef
    pending_interrupt: InterruptEnvelope | None
    error_refs: Annotated[list[ArtifactRef], operator.add]
    event_refs: Annotated[list[ArtifactRef], operator.add]
```

Use a PostgreSQL checkpointer for production durability and a durable artifact
store for raw evidence [SRC-LG-POSTGRES] [SRC-MINIO-VERSIONING]. A node writes an
intent and idempotency key before any side effect, then stores output before
committing the state transition.

## Node Classes

| Class | Examples | LLM permitted | Retry policy |
|---|---|---|---|
| Deterministic validation | Path validation, schema gate, digest check, comparability, citation resolution | No | No retry for invalid input |
| Bounded interpretation | Raw-text extraction, scope suggestion, finding draft, tradeoff critic | Yes | Schema repair and capped provider retry |
| Side-effect worker | Snapshot, build, test, benchmark, collector, analyzer | No by default | Retry transient errors using idempotency key |
| Policy decision | Approval requirement, evidence trust, finding maturity, eligibility | No | Re-evaluate only on new artifact/policy version |
| Human control | Clarification and approval | No | Resume same thread via `interrupt()` [SRC-LG-INTERRUPT] |

## Handoff Traceability

| A1 contract item | A2 obligation | A3 obligation |
|---|---|---|
| Local source identity and snapshot policy | Create and seal exact content snapshot | Analyze only that snapshot |
| Feature ID and scope | Tag evidence and build source/runtime map | Restrict findings and treatments to allowed scope |
| Primary criterion | Collect raw samples and baseline aggregate | Detect problem signal and assess each solution's impact |
| Guardrail | Execute or import baseline guardrail evidence | Evaluate regression risk and validation requirement |
| Workload/dataset/environment | Pin and record every material dimension | Use those dimensions when interpreting evidence |
| Evidence requirement | Bind collector/query/command and coverage status | Cite only accepted evidence IDs |
| Budget | Enforce collection/analyzer/model limits | Stop revision when budget is exhausted |
| Approval and request digest | Verify before collection | Bind all outputs transitively to the same digest |

## End-to-End Acceptance Scenarios

| Scenario | Expected behavior |
|---|---|
| Raw request names a local path and latency target but no workload | A1 extracts fields, discovers context, then interrupts for workload/evidence details |
| Structured request is complete and policy permits automatic intake | A1 bypasses LLM extraction, validates and freezes; A2 starts automatically |
| Git working tree is dirty | A1 records dirty status; A2 creates a content snapshot rather than claiming HEAD alone |
| Repository has tests but no benchmark | A2 runs correctness baseline, reports performance evidence missing and blocks A3 until resolved |
| Imported telemetry lacks commit identity | A2 downgrades trust; it cannot silently pass comparability |
| Static analyzer reports an issue with no measured problem | A3 records an observation but cannot create an eligible optimization solution from it |
| LLM cites a nonexistent evidence ID | Citation resolver rejects the finding and routes to bounded revision |
| A real bottleneck has only one viable treatment | A3 reports one eligible solution and a portfolio insufficiency status; policy decides whether to interrupt or stop |

## RACI

| Activity | Requester | Platform owner | LangGraph | Worker/tool | Approver |
|---|---|---|---|---|---|
| State objective and constraints | R | C | A for workflow record | - | C |
| Validate and normalize A1 | C | A for policy | R | LLM may assist | C |
| Snapshot and collect A2 | I | A for access policy | R for orchestration | R for execution | I |
| Validate evidence/comparability | I | A for rules | R | C | I |
| Draft A3 findings/solutions | I | A for quality policy | R | R for analysis | C |
| Verify claims and eligibility | I | A | R | C | I |
| Approve request or later selection | C | C | R for interrupt/audit | - | A/R |

`A` is accountable, `R` responsible, `C` consulted, and `I` informed.

## Delivery Backlog Boundaries

| Epic | Minimum deliverables |
|---|---|
| EPIC-A1 | Pydantic contracts, raw/structured intake, local-path resolver, Git/source identity, project discovery, generic criteria and guardrails, workload/evidence contract, quality gate, interrupt/approval, immutable artifact |
| EPIC-A2 | Snapshotter, manifest/indexer, collector registry, sandbox preflight, test/benchmark and telemetry adapters, raw artifact store, provenance normalizer, coverage and comparability gates |
| EPIC-A3 | Evidence catalogue, signal detector, analyzer routing, LLM finding/solution adapters, citation resolver, claim judge, risk ladder, treatment/tradeoff validators, revision loop and quality report |
| EPIC-CROSS | PostgreSQL checkpointer, artifact storage, identity/policy integration, secrets broker, event/audit schema, idempotency ledger, observability and cost metering |

## Release Gate for A1-A3

The capability is production-ready only when interrupted runs survive process
restart; duplicate execution is idempotent; a dirty local tree is reproduced
from its snapshot; raw evidence can reconstruct every aggregate; unsupported
LLM claims are rejected; secrets never enter prompts or checkpoints; all A1
criteria are traceable through A2 evidence and A3 assessments; and chaos tests
demonstrate recovery from collector, model and checkpoint failures.
