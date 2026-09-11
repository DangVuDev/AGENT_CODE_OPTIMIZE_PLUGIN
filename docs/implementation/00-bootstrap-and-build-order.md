# 00. Bootstrap and Outside-In Build Order

Status: normative pre-implementation runbook  
Scope: establish the platform frame before implementing A1–A3 nodes  
Code status: no implementation is authorized by this document

## 1. Why This Comes First

Lane 1 is not a collection of independent Python functions. It is the first
business slice inside a durable LangGraph control plane. Therefore construction
must proceed from the runtime boundary inward:

```text
source repository and governance
-> reproducible toolchain
-> local platform dependencies
-> production infrastructure contracts
-> LangGraph root/control-plane frame
-> state, artifact, idempotency and node runtime
-> empty A1/A2/A3 subgraph manifests
-> business-node handlers
-> real adapters and release gates
```

Starting with A1/A2/A3 handler code would force persistence, state, retries,
interrupts, artifacts, security and observability to be retrofitted later. That
is the architecture mismatch this runbook prevents.

## 2. What Is Cloned, Installed, or Pulled

### 2.1 Clone only project-owned repositories

| Repository | Action | Destination | Rule |
|---|---|---|---|
| Optimization platform application | Clone the organization repository when a remote exists; otherwise initialize the current documentation workspace as the new repository | Project root | This is the only mandatory source clone |
| Deployment/GitOps repository | Clone only when the organization deliberately separates application and environment manifests | Sibling workspace, never nested in application source | Production credentials and rendered secrets never enter either repository |
| Policy repository | Clone only when OPA policies have an independent ownership/release lifecycle | Sibling workspace or CI checkout | Pin the policy bundle digest consumed by a case |
| Target codebase under optimization | Register/clone per case into an isolated worker workspace | Ephemeral worker volume | It is untrusted input, never a package dependency of the control plane |

If the platform remote is not yet defined, the first human decision is the
canonical repository URL, owner team, visibility, default branch, signing
policy, required reviews and CODEOWNERS. Do not invent a temporary upstream and
silently migrate history later.

### 2.2 Do not clone framework monorepositories into the project

LangGraph, Pydantic, PostgreSQL checkpointer, OPA clients, OpenTelemetry SDKs
and provider SDKs are versioned dependencies. PostgreSQL, MinIO/S3-compatible
storage, OPA, OpenTelemetry Collector and sandbox components are pinned
container images or managed services. Tree-sitter, Semgrep, CodeQL, Joern,
Slither and other analyzers are isolated tool images/adapters selected later by
repository capability.

Do **not** copy or vendor the LangGraph repository, example agents, MinIO, OPA,
Grafana, or analyzer source trees into this application. Upstream source may be
checked out into a disposable evaluation directory for due diligence, but no
production build may depend on that checkout.

### 2.3 Bootstrap decision table

| Item | Acquisition form | Initial purpose | Required pin |
|---|---|---|---|
| Python | Organization-supported runtime | Control-plane and worker adapter SDK | Exact major/minor and base-image digest |
| `uv` or approved package manager | Installed tool | Environment and lockfile | Exact tool version |
| `langgraph` | Python package | StateGraph, subgraphs, edges, interrupts | Exact version in lockfile |
| `langgraph-checkpoint-postgres` | Python package | Durable production checkpoints | Exact version in lockfile |
| Pydantic | Python package | Runtime contracts and JSON Schema | Exact version in lockfile |
| PostgreSQL | Managed service or image | Checkpoints, case index, intent/idempotency/outbox | Engine version/image digest |
| S3/MinIO-compatible store | Managed service or image | Raw/snapshot/artifact CAS | Service version/image digest and bucket policy |
| OPA or deterministic policy service | Service/image/package behind a port | Hard policy decisions | Bundle/service version and policy digest |
| OpenTelemetry SDK + Collector | Package plus service/image | Control-plane spans/metrics/log routing | Package versions and Collector image digest |
| Kubernetes Job + gVisor/equivalent | Cluster runtime | Isolated collectors/analyzers | Cluster/runtime versions and workload image digests |

Current upstream documentation describes a LangGraph application as a package,
one or more graph exports, dependency metadata, environment configuration, and
optionally `langgraph.json`. The project adopts that shape but owns its deeper
production module boundaries [SRC-LG-APP-STRUCTURE]. LangGraph source itself is
not cloned.

## 3. Stage 0 — Repository and Governance Bootstrap

Owner: platform tech lead and repository administrator.

Required decisions before scaffolding:

1. Establish the canonical application repository and remote origin.
2. Enable protected default branch, signed commits/tags according to company
   policy, required CI, secret scanning and dependency update policy.
3. Add `CODEOWNERS` for contracts, orchestration, policy, security, deployment
   and documentation paths.
4. Define release versioning, schema compatibility and migration ownership.
5. Decide whether deployment and OPA policy live in this repository or separate
   project-owned repositories.
6. Record ADR owners for every component in the adoption register.

Exit gate `BOOT-0`:

- canonical remotes and owners are known;
- branch/review/security policies are enabled;
- the existing blueprint is preserved in the canonical repository; and
- no framework source has been vendored.

### Planned repository bootstrap sequence (not executed)

When the organization repository already exists:

```text
clone <organization>/<optimization-platform> into a clean workspace
verify remote, default branch, signing and CODEOWNERS
bring this reviewed documentation into that repository through a pull request
create the bootstrap branch from the protected default branch
```

When no repository exists:

```text
create the organization-owned empty repository first
clone that empty repository
add this documentation as the first reviewed change
enable governance before adding application scaffolding
```

The literal remote URL cannot be finalized from this workspace because no
organization/repository identity has been supplied. That is a `BOOT-0` input,
not a value the implementation team should guess. The current `D:\OptimizeCode`
directory is also not a Git working tree, so it must not be treated as the
canonical clone until that decision is made.

## 4. Stage 1 — Reproducible Developer Toolchain

Owner: developer-experience/platform team.

The project must define, before application modules:

- supported OS/architecture matrix;
- pinned Python and package-manager versions;
- one lockfile and deterministic dependency resolution;
- format, lint, strict type, unit, contract, integration and security commands;
- pre-commit policy and CI equivalents;
- container engine and local Kubernetes requirement, if any;
- `.env.example` containing development placeholders only and a rule that real
  secrets come from a secret manager or developer-local ignored file; and
- software bill of materials, license and vulnerability scanning steps.

Planned root scaffold (names are contracts, not files created now):

```text
/
|-- docs/
|-- pyproject.toml
|-- uv.lock
|-- langgraph.json
|-- .python-version
|-- .env.example
|-- Makefile or Taskfile.yml
|-- src/production_optimizer/
|-- tests/
|-- schemas/
|-- migrations/
|-- deploy/
|-- policies/
`-- tools/
```

`langgraph.json` exports the root graph factory only. It must not expose A1,
A2, or A3 as separately invokable production workflows that could bypass the
root case lifecycle.

Exit gate `BOOT-1`:

- a clean checkout can create the environment from the lockfile;
- all quality commands run even though no business node exists yet;
- dependency/license/SBOM reports are produced; and
- CI and local commands use the same pinned toolchain.

## 5. Stage 2 — Local Platform Stack and Configuration

Owner: platform infrastructure team.

Create a development-only composition that mirrors production interfaces:

```text
control-plane process
  -> PostgreSQL: LangGraph checkpoint schema
  -> PostgreSQL: case/idempotency/outbox schemas
  -> S3/MinIO-compatible object store: immutable artifacts/raw evidence
  -> OPA policy endpoint or deterministic-policy adapter
  -> OpenTelemetry Collector
  -> isolated worker launcher (local test double, then Kubernetes Job)
```

The composition is not a production deployment. It exists to validate
interfaces, migrations, health checks, readiness, tenant namespaces and
failure behavior before nodes are implemented.

Configuration groups:

| Group | Examples | Storage rule |
|---|---|---|
| Non-secret runtime | environment/profile, endpoint names, timeouts | Versioned config |
| Secret references | database lease, object-store credential ref, model-provider ref | Environment/secret manager reference only |
| Policy versions | OPA bundle digest, risk/approval policy version | Versioned and included in artifacts |
| Tool versions | collector/analyzer/normalizer/model/prompt versions | Lock/adoption manifest |
| Tenant settings | roots, quotas, retention, allowed adapters | Case database with RBAC and audit |

Exit gate `BOOT-2`:

- health/readiness checks succeed;
- migrations are repeatable and have rollback/forward-recovery procedures;
- checkpoint and case data have separate least-privilege roles;
- object-store versioning/retention and tenant prefixes are validated;
- OTel traces reach the development backend without raw source/secrets; and
- shutdown/restart does not lose persisted infrastructure state.

## 6. Stage 3 — Production Infrastructure Contracts

Owner: platform architecture, SRE and security.

Before LangGraph business subgraphs, define and contract-test these ports:

1. `CheckpointProvider`: PostgreSQL-backed in production; in-memory only in
   isolated unit tests. Call setup/migrations explicitly and restrict checkpoint
   deserialization to approved types [SRC-LG-POSTGRES].
2. `CaseRepository`: case status, tenant binding, cancellation and searchable
   indexes; it is not a second workflow engine.
3. `IntentLedger`: idempotency key, pending/completed/unknown outcome,
   reconciliation and callback deduplication.
4. `ArtifactStore`: create-only canonical artifacts, content-addressed blobs,
   digest/signature verification, object lock, retention and tenant isolation.
5. `PolicyPort`: versioned structured input/output; it cannot mutate state or
   call a graph edge.
6. `IdentityPort`: authenticated actor and roles from the host; no caller-supplied
   trust via plain CLI strings.
7. `WorkerBroker`: typed job submission, lease, heartbeat, timeout,
   cancellation, callback and reconciliation.
8. `SecretsBroker`: short-lived reference resolution inside workers only.
9. `TelemetryPort`: node spans and metrics containing references/digests, never
   raw source, evidence or credentials.

The initial worker transport decision must be recorded in an ADR. A recommended
minimal design is a PostgreSQL transactional outbox plus Kubernetes Jobs and a
signed callback/reconciliation loop. Adding Kafka, RabbitMQ or another broker
requires a measured need; it must not become a competing workflow engine.

Exit gate `BOOT-3`:

- each port has a conformance suite and failure taxonomy;
- production and test adapters are visibly separated;
- unknown external-job outcomes reconcile without duplicate execution; and
- security/threat-model review approves the trust boundaries.

## 7. Stage 4 — LangGraph Control-Plane Frame

Owner: control-plane team.

Only now scaffold LangGraph itself:

1. Define the compact root state, reducers, case statuses, artifact references,
   error/event references and interrupt envelope.
2. Build `OptimizationRootGraph` with `initialize_case`, `route_origin`, Lane A,
   Lane B placeholder, shared-flow placeholder and terminal handling.
3. Bind the production checkpointer at composition/deployment time, not inside
   handlers.
4. Implement the universal node runtime:
   precondition -> intent -> idempotency lookup -> execute/reconcile -> artifact
   validation/write -> event -> checkpointed route.
5. Implement common conditional routing, cancellation and dead-letter handling.
6. Export only the root graph/factory through `langgraph.json` and API/MCP.
7. Add framework-level smoke nodes that prove checkpoint, restart, interrupt,
   resume, retry classification, artifact indirection and observability. These
   smoke nodes are test fixtures, not A1/A2/A3 implementations.

Exit gate `FRAME-0`:

- process termination before/after a simulated external effect produces one
  effect and one committed transition;
- a LangGraph interrupt survives restart and resumes the same `thread_id`;
- a parent/subgraph state handoff exposes only intended artifact references;
- reducer results are deterministic under reordered parallel completion;
- checkpoint state contains no raw source/evidence/secret fixture; and
- topology and telemetry can be inspected without executing Lane 1.

## 8. Stage 5 — Empty Lane A Graph Manifest

Owner: Lane A/control-plane team.

Create compiled `LaneASubgraph`, `A1RequirementSubgraph`,
`A2BaselineSubgraph`, and `A3SolutionSubgraph` definitions from a reviewed node
manifest. Register all 54 task IDs and every documented edge, fan-out, fan-in,
interrupt and bounded revision path. Handlers initially remain unavailable and
the graph cannot be released or invoked for business use.

This stage validates architecture, not business behavior. It prevents later
handler work from collapsing tasks into `run_a1`, `run_a2`, `run_a3`, or
`execute_a1_a2_a3`.

Exit gate `FRAME-1`:

- compiled node inventory equals the 54-node catalogue;
- every route target exists and every loop has a bound;
- stage IDs are compiled subgraphs, not executable service nodes;
- only `OptimizationRootGraph` is publicly invokable; and
- a topology snapshot is generated and compared in CI.

## 9. Stage 6 — Implement Lane 1 from A1 to A3

Only after `BOOT-0` through `FRAME-1` pass:

1. Implement A1.10–A1.95 in order, with clarification and approval resume tests.
2. Implement A2.10–A2.50 control nodes, then its isolated collector branches,
   deterministic fan-in, quality/comparability gates and seal node.
3. Implement A3.10–A3.51 evidence/finding path, then A3.60–A3.82 strategy and
   revision path, then A3.90 sealing.
4. Run stage contract tests before connecting the next stage.
5. Run one real repository reconstruction only after all A1–A3 negative paths
   pass; a happy-path demo is not an early release gate.

Node-level details remain in
[`lane-1-langgraph-architecture.md`](lane-1-langgraph-architecture.md).

## 10. Stage 7 — Production Adapter Graduation

Development adapters cannot graduate by configuration rename. Each adopted
adapter needs an ADR recording exact version/digest, license, owner, SBOM/CVE
process, topology, data residency, permissions, costs, benchmarks, failure
modes, fallback and removal plan.

Graduation order for Lane 1:

1. PostgreSQL checkpointer, case store and idempotency/outbox.
2. Immutable object storage and KMS signing.
3. Identity/RBAC and policy service.
4. Isolated worker broker and source snapshotter.
5. Repository indexing and native build/test discovery.
6. Prometheus/Loki/Tempo/Pyroscope/MLflow adapters required by the first real
   workload.
7. Tree-sitter and the smallest applicable analyzer set.
8. LLM generator and independent judge adapters after A1/A2 preflight and
   deterministic gates are already working.

Exit gate `LANE1-PROD` is the M0/M1 release gate, not “all handlers compile.”
It requires resume, idempotency, recovery, tenant isolation, prompt/tool abuse,
collector failure, unsupported claim, digest chain, load/chaos and operational
runbook evidence.

## 11. Ordered Deliverables

| Order | Deliverable | Depends on | Business nodes allowed? |
|---|---|---|---|
| 0 | Canonical repository/governance | Nothing | No |
| 1 | Pinned toolchain, lock strategy and CI skeleton | 0 | No |
| 2 | Local platform composition and configuration contract | 1 | No |
| 3 | Infrastructure ports, migrations and conformance tests | 2 | No |
| 4 | Root LangGraph/node-runtime smoke frame | 3 | Test fixture nodes only |
| 5 | Exhaustive empty Lane A topology | 4 | Registered but unavailable |
| 6 | Canonical schemas/artifacts and A1 handlers | 5 | A1 only |
| 7 | A2 control path, workers and evidence gates | 6 | A1–A2 |
| 8 | A3 analysis, judge, strategy and revision path | 7 | A1–A3 |
| 9 | API/CLI/MCP one-thread surfaces | 8 | Full Lane 1 |
| 10 | Production adapter graduation and release evidence | 9 | Suggest-only production |

No later row may be used to bypass an unmet earlier gate.
