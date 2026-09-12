# 05. Framework and Open-Source Mapping

## Workflow Ownership Rule

Every row in this matrix describes a capability used *inside* a LangGraph node.
It does not delegate workflow ownership to the listed framework. Stage labels
such as `A2` are shorthand for the task nodes in that stage's business
catalogue: the stage itself is a compiled subgraph, not one coarse executable
node. Only a LangGraph node may validate adapter output, write graph-state
references, apply the retry contract, or select a conditional edge.

For the exhaustive A1.10–A3.90 mapping from node to capability provider,
decision authority, and side-effect/retry class, see
[`../implementation/05-lane-1-detailed-implementation-playbook.md`](../implementation/05-lane-1-detailed-implementation-playbook.md).
For Lane B reuse, historical discovery, C0 and the full delivery order, see
[`../implementation/02-two-lane-product-architecture.md`](../implementation/02-two-lane-product-architecture.md)
and
[`../implementation/03-two-lane-delivery-roadmap.md`](../implementation/03-two-lane-delivery-roadmap.md).

## Primary Matrix

| Node | Business problem | Recommended framework | What it solves | Remaining weakness | Complement |
|---|---|---|---|---|---|
| All | Stateful orchestration, resume, and HITL | LangGraph `[SRC-LG-OVERVIEW]` | StateGraph, conditional edges, checkpoints, and interrupts | Does not provide telemetry, analysis, sandboxing, or policy | PostgreSQL, object storage, OPA, worker platform |
| A1 | Typed generic intake | Pydantic and JSON Schema `[SRC-PYDANTIC]` `[SRC-JSON-SCHEMA]` | Validates objectives, criteria, and scope; generates schemas | Does not understand business features or RBAC | Feature/metric registry, OPA, identity provider |
| A1 | Clarification and approval | LangGraph interrupt `[SRC-LG-INTERRUPT]` | Thread-bound pause and resume | Code before the interrupt may replay | Idempotency ledger and signed approval |
| A1/A2/A3/B1/B2/C0 | Hard business gates | OPA or deterministic Python `[SRC-OPA]` | Versioned structured policy decisions | Cannot create missing evidence, ownership or business policy | Golden tests, simulation, RBAC and immutable decision artifacts |
| A2/B1 | Trace, metric, and log identity | OpenTelemetry `[SRC-OTEL]` | Instruments and correlates signals | Is not a storage or query backend | Prometheus, Loki, Tempo |
| A2/B1 | Metrics and SLOs | Prometheus `[SRC-PROM]` | Time series and PromQL | Cardinality risk and no automatic code mapping | OTel conventions and Pyroscope |
| A2/B1 | Logs | Loki `[SRC-LOKI]` | Historical logs and LogQL | Logs alone do not prove latency or root cause | Metrics plus trace and profile IDs |
| A2/B1 | Distributed traces | Tempo `[SRC-TEMPO]` | Trace search and log/metric linking | Requires consistent instrumentation | OTel plus service and commit tags |
| A2/B1/A3 | CPU and memory hotspots | Pyroscope `[SRC-PYROSCOPE]` | Continuous profiling linked to source | Coverage/overhead concerns; no correctness proof | Native tests and business metrics |
| A2/B1/05 | LLM traces and evaluation | MLflow GenAI `[SRC-MLFLOW]` `[SRC-MLFLOW-EVAL]` | Tokens, latency, scoring, and trace evaluation | Another datastore; scorers may be wrong | OTel transport and holdout datasets |
| A2/A3 | Multi-language syntax index | Tree-sitter `[SRC-TREE]` | CST, symbols, and source locations | Incomplete type and data-flow semantics | Native analyzers, CodeQL, or Joern |
| A3/04 | Pattern and static findings | Semgrep `[SRC-SEMGREP]` | Deterministic rules and local scans | Not a general performance profiler | Profiles, tests, and owned rules |
| A3/04 | Semantic security queries | CodeQL `[SRC-CODEQL]` | Queryable code database and data flow | Extraction cost and language boundaries | Fast Tree-sitter index and runtime evidence |
| A3 | Code property graph | Joern `[SRC-JOERN]` | Cross-file graph queries | Operational complexity and false positives | Native analyzers and evidence judge |
| A3/04/05 | Solidity analysis and measurement | Slither and Foundry `[SRC-SLITHER]` `[SRC-FOUNDRY]` | Static detectors, tests, traces, and gas | Compilation dependency and false positives | Pinned toolchain and invariant tests |
| 01/02/03/06/08 | Policy | OPA `[SRC-OPA]` | Structured policy-as-code decisions | Policy quality remains company-owned | Versioning, review, and simulation |
| 02 | Plan drafting and criticism | LLM with a LangGraph subgraph | Draft, critic, and revision cycle | May invent files or commands | Manifest resolver and deterministic validator |
| 03 config | Configuration search | Optuna `[SRC-OPTUNA]` | Sampling and pruning in a typed search space | Can optimize the wrong objective or waste trials | A1 metric gates, budget, and sandbox |
| 03 prompt | Prompt and RAG optimization | DSPy `[SRC-DSPY]` | Optimizes an LM program against a metric and dataset | Overfitting, token cost, and scorer dependence | Holdout evaluation and cost/safety guardrails |
| 03 function | Function-level performance | Codeflash `[SRC-CODEFLASH]` | Candidate generation, tests, repeated benchmarks | Language/scope limits and cloud dependency | Optional adapter and independent measurement |
| 03 code | Cross-file coding | OpenHands `[SRC-OPENHANDS]` | Agent SDK, tools, and remote workspaces | Non-deterministic and cannot prove experiment validity | LangGraph policy, sandbox, and steps 04-05 |
| 03 code | Claude implementation | Claude Agent SDK `[SRC-CLAUDE]` | Agent loop and read/edit/command tools | Vendor cost and data-governance concerns | Broker, budget, sandbox, provider abstraction |
| 03 code | Codex implementation | Codex tool or SDK adapter `[SRC-CODEX]` | Workspace-scoped coding tasks | Provider dependency and approval semantics | Typed adapter, scope gate, independent tests |
| 03 code | Alternative coding agent | SWE-agent or mini-SWE-agent `[SRC-SWEAGENT]` | Issue-to-patch workflow | Does not cover the optimization lifecycle | Use only as an implementation backend |
| 03-05 | Isolated execution | Kubernetes Job and gVisor `[SRC-K8S-JOB]` `[SRC-GVISOR]` | Bounded jobs, retries, resources, and isolation | Kubernetes is not itself a security boundary; compatibility costs | Deny egress, short-lived secrets, stronger isolation when required |
| 04 | Disposable dependencies | Testcontainers `[SRC-TESTCONTAINERS]` | Containerized integration-test dependencies | Docker dependency and startup cost | Pinned images, cache, and timeouts |
| 05 | Load and threshold testing | k6 `[SRC-K6]` | Workloads and pass/fail metric thresholds | Does not prove before/after comparability | Custom comparability validator |
| 05 | Benchmark history | Bencher `[SRC-BENCHER]` | Stores history and detects threshold regressions | Does not select the correct workload or baseline | Native harness plus A2/A5 policy |
| 07 | Immutable artifacts | S3/MinIO versioning and object lock `[SRC-MINIO-VERSIONING]` `[SRC-MINIO-LOCK]` | Retention and recovery | Does not create provenance | Digest chain and signed manifest |
| 08 | Progressive delivery | Argo Rollouts `[SRC-ARGO]` | Canary/blue-green promotion and rollback | Owns delivery, not the business decision | LangGraph, Prometheus, and OPA |
| 08 | Feature exposure | OpenFeature `[SRC-OPENFEATURE]` | Vendor-neutral feature-flag API | Requires a provider and governance | Rollout controller, flag provider, and audit |

## Production Adoption Register

This register converts recommendations into explicit architecture status. An
`Accepted target` is mandatory for the intended production profile but is not a
claim that the adapter is already implemented.

| Capability | Selected component | Status | Profile | Exit gate before adoption |
|---|---|---|---|---|
| Workflow orchestration | LangGraph `[SRC-LG-OVERVIEW]` | Accepted target | Both | Root/subgraph conformance, crash/resume and interrupt tests |
| Runtime contracts | Pydantic + generated JSON Schema `[SRC-PYDANTIC]` `[SRC-JSON-SCHEMA]` | Accepted target | Both | Canonical models, compatibility fixtures and stable digest tests |
| Durable checkpoint | LangGraph PostgreSQL checkpointer `[SRC-LG-POSTGRES]` | Accepted target | Production | HA, backup/restore, migration and failover tests |
| Policy authority | OPA `[SRC-OPA]` or deterministic Python policy behind one port | Decision required by M0 | Both | Golden policy tests, simulation, RBAC and versioning |
| Local source identity | Git CLI `[SRC-GIT]` + content-addressed snapshotter | Accepted target | Both | Dirty/untracked/submodule reproduction and path-safety tests |
| Syntax index | Tree-sitter `[SRC-TREE]` | Conditional accepted target | Both | Language coverage PoC and partial-parse reporting |
| Fast static analysis | Semgrep `[SRC-SEMGREP]` | Conditional accepted target | Both | Owned rules, false-positive baseline and sandboxed CLI contract |
| Deep semantic analysis | CodeQL `[SRC-CODEQL]` or Joern `[SRC-JOERN]` adapter | Evaluate per language | Both | Accuracy/cost benchmark and license/security review |
| Solidity | Slither + Foundry `[SRC-SLITHER]` `[SRC-FOUNDRY]` | Conditional by repository | Both | Pinned compiler/toolchain and invariant/gas fixtures |
| Metrics/logs/traces/profile | Prometheus/Loki/Tempo/Pyroscope `[SRC-PROM]` `[SRC-LOKI]` `[SRC-TEMPO]` `[SRC-PYROSCOPE]` | Conditional by evidence contract | Connected production | Read-only auth, provenance labels, query budgets and retention review |
| LLM tracing/evaluation | MLflow GenAI `[SRC-MLFLOW]` `[SRC-MLFLOW-EVAL]` | Conditional by LLM feature | Both | Dataset/scorer governance and data-residency review |
| Coding executor | OpenHands default; Claude/Codex optional `[SRC-OPENHANDS]` `[SRC-CLAUDE]` `[SRC-CODEX]` | Evaluate in M4 | Local sandbox/production | Patch-quality benchmark, permission isolation, cost and provider-governance review |
| Sandbox | Kubernetes Job + gVisor `[SRC-K8S-JOB]` `[SRC-GVISOR]` | Accepted target for connected production | Production | Escape/threat model, deny egress, quota, cleanup and compatibility tests |
| Benchmark history/load | Bencher + k6 `[SRC-BENCHER]` `[SRC-K6]` where applicable | Conditional | Both | A/A noise characterization and repository workload validation |
| Rollout | Argo Rollouts + OpenFeature `[SRC-ARGO]` `[SRC-OPENFEATURE]` | Conditional | Connected production only | Canary, kill-switch and rollback drill |
| Artifact storage | S3/MinIO-compatible versioned store `[SRC-MINIO-VERSIONING]` `[SRC-MINIO-LOCK]` | Accepted target | Production | Encryption, object-lock, backup/restore and tenant-isolation tests |

## Required Due-Diligence Record

Before an adapter moves to `Adopted`, its ADR records exact package/image
version and digest, license and legal owner, support lifecycle, CVE/SBOM process,
deployment topology, data residency, network/secrets needs, benchmark results,
operational cost, failure modes, fallback and removal plan. Versions belong in
lockfiles and deployment manifests; this document records the approved range
and decision rationale rather than a drifting latest version.

## Ownership Boundaries

```text
LangGraph: workflow state and transitions
OPA or Python policy: deterministic decisions
Observability stack: raw runtime evidence
Code analyzers: source evidence
Coding agent or optimizer: candidate patch
Sandbox platform: execution boundary
Native tests and benchmarks: correctness and performance observations
Argo and OpenFeature: deployment mechanism
Company control plane: identity, comparability, risk, approval, and audit
```

## What Can Be Reused

- Orchestration primitives: LangGraph.
- Telemetry transport and storage: OpenTelemetry, Grafana stack, Prometheus.
- Parsing and analysis: Tree-sitter, Semgrep, CodeQL, Joern, and Slither.
- Optimization engines: Optuna, DSPy, and Codeflash.
- Coding agents: OpenHands, Claude Agent SDK, Codex adapter, and SWE-agent.
- Sandbox and jobs: Kubernetes and gVisor.
- Verification and delivery: native test tools, k6, Bencher, Argo Rollouts, and
  OpenFeature.

## What Must Be Built In-House

- Feature and metric identity plus the query registry.
- A1 contracts and approval policy.
- Evidence provenance and comparability.
- Verified-cause quality gates.
- Risk ladder and solution re-ranking.
- The one-treatment rule.
- KEEP, FIX_ONE_PART, and REVERT policy.
- Rollout and rollback policy plus the audit chain.

## Licensing and Cost

Not every option is free in every deployment model. Many core projects are open
source, while hosted services, enterprise features, model APIs, compute,
storage, and operations still cost money. Pin the exact component and version,
then perform legal review before adoption. See [11-sources.md](11-sources.md).
