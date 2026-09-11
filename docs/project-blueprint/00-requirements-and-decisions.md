# 00. Requirements and Architecture Decisions

## Requirement Baseline

| ID | Requirement | Acceptance evidence |
|---|---|---|
| REQ-FLOW-001 | Support manual Lane A and automatic Lane B, converging before selection | Root-graph topology and C0 contract test |
| REQ-FLOW-002 | LangGraph manages every business transition, retry, interrupt and approval from intake through rollout | Checkpointed node inventory and resume tests |
| REQ-A1-001 | Accept generic natural-language or structured optimization requests for a local codebase | A1 schema, extraction and validation scenarios |
| REQ-A1-002 | Define measurable primary criteria, correctness guardrails, scope and workload before baseline collection | Approved `OptimizationRequest` artifact |
| REQ-A2-001 | Collect real evidence for the exact feature and immutable source snapshot; never synthesize baseline values | Raw evidence, provenance and digest chain |
| REQ-A2-002 | Reject missing, stale or incomparable evidence | A2 quality and comparability reports |
| REQ-A3-001 | Produce evidence-bound findings and materially different solution strategies with pros, cons and risk | Grounded solution package and quality report |
| REQ-B1-001 | Discover opportunities only from historical observations already available | Scan audit proving read-only historical queries |
| REQ-B2-001 | Apply the same A3 analysis and quality gates to automatic proposals | Shared-subgraph identity and contract tests |
| REQ-EXP-001 | Change and measure exactly one logical treatment per experiment phase | Diff/treatment isolation report |
| REQ-VERIFY-001 | Build and test immediately after each implementation phase | Verification artifact per phase |
| REQ-MEASURE-001 | Compare before/after only when all material dimensions are compatible | Comparable measurement report |
| REQ-DECIDE-001 | Derive KEEP, FIX_ONE_PART or REVERT from deterministic policy and measured evidence | Decision trace and policy version |
| REQ-ROLLBACK-001 | Provide and verify executable rollback at every state-changing stage | Restoration evidence |
| REQ-AUDIT-001 | Reconstruct every decision from immutable, schema-versioned artifacts | Audit-chain verification report |
| REQ-SEC-001 | Isolate untrusted repositories and least-privilege all tools, network and secrets | Sandbox and authorization tests |
| REQ-OPS-001 | Resume safely after process/worker failure without duplicate side effects | Crash/recovery and idempotency tests |

## Scope Profiles

| Profile | Source | Evidence | Maximum outcome |
|---|---|---|---|
| `local_analysis` | Registered local directory or Git working tree | Local execution and imported/connected historical evidence | A3 proposal, or local sandbox patch through 06 when explicitly enabled |
| `connected_production` | Immutable local snapshot corresponding to a deployable repository revision | Approved production observability and deployment systems | Full workflow through progressive rollout |

`local_analysis` is the initial product scope. It cannot claim production
rollout merely because a local working tree changed. Step 08 is available only
under `connected_production` with a declared deployment adapter.

## Architecture Decisions

| ID | Decision | Status | Consequence |
|---|---|---|---|
| DEC-ARCH-001 | Use one root LangGraph with Lane A, Lane B and shared subgraphs | Accepted target | External adapters return typed results and never choose graph routes |
| DEC-ARCH-002 | Keep raw evidence and source snapshots outside graph checkpoints | Accepted target | State stores only compact artifact references |
| DEC-ARCH-003 | Separate multi-candidate B1 scan threads from per-opportunity B2 case threads using a sealed opportunity and transactional case-start outbox | Accepted target | One scan may create many isolated cases; delivery invokes the same root graph and cannot choose a route |
| DEC-DATA-001 | Represent artifact identity with `artifact_type` plus semantic `schema_version` | Accepted | Do not encode the version into the artifact type string |
| DEC-DATA-002 | Use canonical artifact names from `06-data-contracts.md` | Accepted | Wrapper packages are envelopes, not alternative artifact types |
| DEC-EXP-001 | A solution is a strategy containing one or more ordered experiment phases; each phase contains exactly one treatment | Accepted | Planning may be multi-phase without violating one-variable measurement |
| DEC-EVIDENCE-001 | Permit diagnostic experiments from strong hypotheses, but permit production implementation only from verified causes or successful diagnostic evidence | Accepted | A3 can progress without pretending correlation is causality |
| DEC-POLICY-001 | Deterministic policy owns hard gates and operational decisions | Accepted | LLMs may propose and explain but never self-authorize |
| DEC-SCOPE-001 | Deliver `local_analysis` first; integrate production deployment later | Accepted | Remote Git and rollout adapters are extension capabilities |
| DEC-FRAMEWORK-001 | Adopt frameworks through capability adapters and conformance tests rather than direct workflow coupling | Accepted | Components remain replaceable and cannot bypass LangGraph |
| DEC-OPS-001 | Use PostgreSQL checkpoints and immutable object storage in production | Accepted target | SQLite/filesystem remain development-only |

## Traceability Matrix

| Requirement | Business nodes | Canonical artifacts | Release milestone |
|---|---|---|---|
| REQ-FLOW-001, REQ-FLOW-002 | A1-A3, B1-B2, C0-08 | `WorkflowState`, all stage reports | M0-M3 |
| REQ-A1-001, REQ-A1-002 | A1.10-A1.95 | `OptimizationRequest` | M1 |
| REQ-A2-001, REQ-A2-002 | A2.10-A2.95 | `BaselineSnapshot`, `EvidenceBundle`, `ComparabilityReport` | M1 |
| REQ-A3-001 | A3.10-A3.90 | `FindingSet`, `SolutionPortfolio`, `A3QualityReport` | M1 |
| REQ-B1-001, REQ-B2-001 | B1.10-B1.96, B2.10-B2.60 | `QualifiedOpportunity`, A3 artifacts | M2 |
| REQ-EXP-001 | S02.40, S03.50, S05.20 | `ExecutionPlan`, `Treatment`, `IsolationReport` | M3-M5 |
| REQ-VERIFY-001 | S04.10-S04.90 | `VerificationReport` | M4 |
| REQ-MEASURE-001 | S05.10-S05.90 | `Measurement`, `ComparabilityReport` | M5 |
| REQ-DECIDE-001, REQ-ROLLBACK-001 | S06.10-S06.80, S08.40/S08.80 | `Decision`, `RollbackReport` | M5-M6 |
| REQ-AUDIT-001 | All nodes, S07 | `OptimizationReport`, audit envelope | M0-M6 |
| REQ-SEC-001, REQ-OPS-001 | Cross-cutting and S03-S08 | Authorization, lease, checkpoint and recovery reports | M0/M4/M7 |

## Change Control

Changing an accepted `REQ-*` or `DEC-*` requires an owner, rationale, impact
analysis, replacement or superseding ID, and updates to affected contracts,
business rules, tests and roadmap gates. Historical artifacts continue to refer
to the policy and decision versions under which they were created.
