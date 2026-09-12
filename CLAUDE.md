# Production Optimizer — Project Handbook for Claude

## Project Overview

**production-optimizer** is a LangGraph-based control plane for evidence-grounded code optimization. It orchestrates a 99-node business workflow across two lanes (manual and automatic) that analyze source code, measure baselines, discover opportunities, propose solutions, and converge on a final optimization decision.

**Current State (2026-09-12 — all 99 business nodes have real production handlers):**
- ✅ LangGraph topology: 99 business nodes compiled across A1/A2/A3/B1/B2/C0
- ✅ Control-plane ports: 10 protocol definitions (case/intent/outbox/artifact store/policy/identity/secrets/telemetry/worker/checkpoint)
- ✅ Production adapters: Postgres (case, intent, outbox, migrations), S3 (artifact store), Python (policy), JWT (identity), OTel (telemetry), local worker broker, Kubernetes worker broker (implemented + unit-tested, not wired into any script or run against a live cluster)
- ✅ 5 LLM model-provider adapters: Anthropic, OpenAI, Gemini, DeepSeek, Ollama (`adapters/production/*_model_provider.py`)
- ✅ Business contracts: A1–C0 sealed artifacts + leaf models, 150+ classes, full Pydantic + JSON Schema support (`schemas/`)
- ✅ Universal node execution: NodeRuntime with intent idempotency, artifact lifecycle, telemetry emission
- ✅ **Business node handlers: all 99 nodes implemented and registered** — `application/{a1,a2,a3,b1,b2,c0}_handlers.py`, each with real, passing contract tests (`tests/contract/test_{a1,a2,a3,b1,b2,c0}_production_handlers.py`)
- ✅ Lane A is a real, end-to-end-runnable optimizer: `scripts/optimize.py <repo> --objective "..."` runs A1→A2→A3→C0 against any real repository (git/pytest/ruff/`act`-CI/pytest-benchmark execution + a real LLM call)
- ✅ Lane B (B1 discovery, B2 proposal) has equally real handler logic, including B1.95/B2.22 mounting the same real A2/A3 subgraphs Lane A uses — but **no scheduler/CLI entrypoint exists yet**; it has only been exercised through contract tests that seed state directly
- ✅ Human-in-the-loop resume/approval: `application/resume.py`, used by A1.90, A2.31 (LLM-suggested command approval), and B2's owner-review routing
- ✅ Node manifest: 99 task IDs → input/output contract bindings (`orchestration/manifest.py`, `manifests/node-manifest.json`)
- ⏳ B1.32-35 (historical metrics/logs/traces/LLM-evidence queries): no adapter wired into `NodePorts` yet — always honestly report `unavailable_reason`, by deliberate scope decision, not an oversight
- ⏳ Kubernetes worker broker: implemented and unit-tested, but unwired/unvalidated against a live cluster — `LocalWorkerBroker` is what every script actually uses
- ⏳ OPA policy adapter: deferred (Python deterministic policy is primary for now)
- ⏳ Differentiated C0 failure routing (route back to producer stage vs. A3/B2 vs. close-case, per the convergence doc's aspirational design): the compiled graph only supports one `rejected` outcome today; reasons are recorded in `ConvergenceDecision.reasons`
- ⏳ No public API/MCP surface

---

## Critical Architectural Decisions (from this session)

### 1. Port Injection & Handler Signature
- `BusinessNodeHandler` now takes TWO parameters: `(state: OptimizationState, ports: NodePorts | None)`
- `NodePorts = dataclass(artifacts, intents, telemetry)` — only non-`PURE` side-effect nodes get a live bundle
- `PURE` nodes (deterministic, no side effects) get `ports=None` and are always recomputed, never cached
- ✅ **All existing tests updated** to match new signature

### 2. Intent Idempotency & Reconciliation
- **IntentLedger protocol signature changed**: all four methods now require `tenant_id: str` as keyword-only param
- Intent lifecycle: `prepare(pending)` → `execute()` → `complete(output_ref)` OR `mark_unknown(crash_signal)`
- Reconciliation: calling `prepare()` twice with the same idempotency key is safe (upsert semantics for pending, no-op for completed)
- **Test proof in `test_control_plane_restart.py`**: a process crash after execution but before checkpoint commit is recoverable via `mark_unknown()` → reconciliation loop

### 3. Contract Typing & Sealed Artifacts
- All business artifacts inherit `ArtifactEnvelope(ContractModel)` and pin `artifact_type`/`schema_version` as `Literal[...]`
- Pydantic strict mode flag: `reportIncompatibleVariableOverride = false` in pyproject.toml (discriminated-union pattern expected)
- Business-stage models (a1–c0) deliberately NOT exported from `contracts/__init__.py` to avoid 60+ symbol collision; import directly: `from production_optimizer.contracts.a3 import FindingSet`
- Cross-artifact linkage via `*_digest: str` fields (not nested objects, not `ArtifactRef`) — matches existing a1/a2 pattern

### 4. Postgres Migrations & Bootstrap
- Two migration files: `001_control_plane.sql` (initial schema) + `002_node_intents_artifact_ref.sql` (adds `output_schema_version`, `output_uri`)
- `apply_migrations(dsn)` is idempotent: skips already-applied migrations (version + checksum match), safe to call every test run
- Each migration wrapped in its own transaction; runner re-wraps in one outer transaction including the `schema_migrations` INSERT
- **Entry point**: `scripts/migrate.py` reads `OPTIMIZER_DATABASE_DSN` from environment, no other config needed

### 5. Coverage Policy
- Coverage omit list still includes `adapters/*`, `ports/*`, `contracts/platform.py` (pre-existing gate: 90% branch coverage fail_under)
- New adapter+contract unit tests are written but not required to break the gate (out of scope for FRAME-0 gate)
- Rationale: integration tests prove adapters work; omit list deferred to post-Stage-7 (when all adapters proven)

---

## File Structure & Key Roles

### Core Ports (Interfaces)
```
src/production_optimizer/ports/
├── __init__.py              # Exports 10 protocol names
├── artifacts.py             # ArtifactStore (put_json/put_blob/read/verify)
├── cases.py                 # CaseRepository (create/get/set_status/request_cancellation)
├── checkpoints.py           # CheckpointProvider (setup/checkpointer/healthcheck/close)
├── identity.py              # IdentityPort (authenticate/authorize)
├── intents.py               # IntentLedger (prepare/get/complete/mark_unknown) ⚠️ SIGNATURE CHANGED
├── outbox.py                # OutboxPort (publish/claim/mark_delivered/mark_failed) ⚠️ NEW
├── policy.py                # PolicyPort (evaluate/healthcheck)
├── secrets.py               # SecretsBroker (lease context manager)
├── telemetry.py             # TelemetryPort (emit/flush)
└── workers.py               # WorkerBroker (submit/cancel/reconcile)
```

### Production Adapters (Implementations)
```
src/production_optimizer/adapters/production/
├── __init__.py                     # Exports adapter classes + type aliases
├── migrations.py                   # apply_migrations(dsn), MigrationError
├── postgres_case_repository.py     # PostgresCaseRepository
├── postgres_intent_ledger.py       # PostgresIntentLedger
├── postgres_outbox.py              # PostgresOutboxAdapter
├── s3_artifact_store.py            # S3ArtifactStore
├── python_policy.py                # DeterministicPythonPolicy
├── jwt_identity.py                 # JwtIdentityPort
├── otel_telemetry.py               # OtelTelemetryPort
├── local_worker_broker.py          # LocalWorkerBroker (in-process; used by every real script)
├── kubernetes_worker_broker.py     # KubernetesWorkerBroker (real, unit-tested; unwired/never run against a live cluster)
├── anthropic_model_provider.py     # AnthropicModelProvider
├── openai_model_provider.py        # OpenAIModelProvider
├── deepseek_model_provider.py      # DeepSeekModelProvider (OpenAI-compatible)
├── ollama_model_provider.py        # OllamaModelProvider (OpenAI-compatible, local)
├── gemini_model_provider.py        # GeminiModelProvider
├── _openai_compatible.py           # Shared chat-completion helper for the OpenAI-compatible providers
└── postgres_checkpoint.py          # PostgreSQL LangGraph checkpointer
```

### Contracts (Business Data Models)
```
src/production_optimizer/contracts/
├── __init__.py              # Exports cross-cutting types (not stage-specific)
├── base.py                  # ContractModel base
├── artifacts.py             # ArtifactRef
├── envelope.py              # ArtifactEnvelope, ProducerIdentity
├── platform.py              # IntentRecord, IntentStatus, OutboxRecord, PolicyRequest/Decision, etc.
├── canonical.py             # sha256_digest, canonical_json, model_content_digest
├── state.py                 # OptimizationState TypedDict + reducers (merge_artifact_refs, merge_node_routes, ...)
├── a1.py                    # OptimizationRequest + leaf types (real handlers: application/a1_handlers.py)
├── a2.py                    # SourceSnapshot, BaselineSnapshot, EvidenceBundle, etc. (real handlers: a2_handlers.py)
├── a3.py                    # FindingSet, SolutionPortfolio, A3QualityReport, ~45 classes (real handlers: a3_handlers.py)
├── b1.py                    # DetectionReport, QualifiedOpportunity, ~18 classes (real handlers: b1_handlers.py)
├── b2.py                    # ProposalEnvelope, ~9 classes (real handlers: b2_handlers.py)
├── c0.py                    # ConvergenceDecision, ConvergedCase, 6 classes (real handlers: c0_handlers.py)
├── registries.py            # Registry/registration record types (RegisteredSource, etc.)
└── [commands|errors|events|interrupts].py  # Other platform types
```

### Node Orchestration (Application Layer)
```
src/production_optimizer/application/
├── __init__.py              # Exports build_{a1,a2,a3,b1,b2,c0,pilot}_{registrations,runtime}, NodePorts, etc.
├── node_runtime.py          # NodeRuntime, NodePorts, NodeExecution, NodeRoute
│                             # Protocol: full universal execution (intent→idempotency→execute→artifact→telemetry)
├── node_contract.py         # NodeSpec, SideEffectClass
├── a1_handlers.py           # Real A1 (Requirement Intake) production handlers
├── a2_handlers.py           # Real A2 (Real Baseline) production handlers + A2_BLOCKED_NODES (now empty)
├── a2_worker_capabilities.py # LocalWorkerBroker capability functions A2 dispatches to (pytest/ruff/act/benchmark)
├── a3_handlers.py           # Real A3 (Grounded Solutions) production handlers, incl. bounded revision loop
├── b1_handlers.py           # Real B1 (Automatic Discovery) production handlers; merges in real A2 at B1.95
├── b2_handlers.py           # Real B2 (Automatic Proposal) production handlers; merges in real A3 at B2.22
├── c0_handlers.py           # Real C0 (shared convergence gate) production handlers, used by both lanes
├── resume.py                # resume_case(): generic human-in-the-loop interrupt/resume for any halting node
├── registry.py              # RegistryPort resolution helpers
└── pilot_handlers.py        # Deterministic in-process stand-in for all 99 nodes (topology/routing tests only)
```

---

## Testing Structure

### Unit Tests (no external infra needed)
```
tests/unit/
├── test_contracts_a3.py, test_contracts_b1.py, etc.    # Pydantic contract shape/validator tests
├── test_canonical_digest.py                             # sha256_digest/canonical_json/model_content_digest
├── test_schema_compatibility.py                         # JSON Schema generation proof
├── test_migrations.py                                   # migration ordering, drift detection
├── test_postgres_adapters_helpers.py                    # ArtifactRef reconstruction, etc.
├── test_s3_artifact_store.py                            # in-memory S3Mock, no real S3
├── test_python_policy.py                                # golden-decision tests
├── test_jwt_identity.py                                 # token encoding/decoding
├── test_otel_telemetry.py                               # InMemorySpanExporter, no network
├── test_local_worker_broker.py                          # thread pool, timeout, cancel
├── test_kubernetes_worker_broker.py                     # restricted Job spec, idempotent submit, reconcile
├── test_node_runtime.py                                 # 2-param handler signature, idempotency key derivation
└── [other pre-existing tests]
```

### Contract Tests (real production handlers, in-memory ports, no external infra)
```
tests/contract/
├── test_a1_production_handlers.py      # Real A1 handlers end to end
├── test_a2_production_handlers.py      # Real A2: git/pytest/ruff/act/pytest-benchmark via LocalWorkerBroker
├── test_a3_production_handlers.py      # Real A3: scripted model provider, revision loop, quality gates
├── test_b1_production_handlers.py      # Real B1: 25 native nodes + embedded real A2 at B1.95
├── test_b2_production_handlers.py      # Real B2: 11 native nodes + embedded real A3 at B2.22
├── test_c0_production_handlers.py      # Real C0: schema/digest-chain/equivalence/freshness/gate, both lanes
├── test_pilot_flow.py                  # Deterministic pilot runtime (topology proof only, no real I/O)
└── test_root_graph.py                  # Compiled graph inventory vs. catalog (all 99 node IDs)
```

### Integration Tests (skip-if-unreachable pattern)
```
tests/integration/
├── test_postgres_case_repository.py    # Postgres + migrations required
├── test_postgres_intent_ledger.py      # core idempotency + restart proof
├── test_postgres_outbox.py             # transactional outbox proof
├── test_s3_artifact_store_live.py      # real S3/MinIO round-trip, optional
├── test_control_plane_restart.py       # CRITICAL: crash-recovery + no-dup-side-effects
├── test_anthropic_model_provider.py, test_openai_compatible_model_providers.py, test_gemini_model_provider.py
│                                        # real LLM calls, skip if no API key/local server reachable
└── conftest.py                         # (does NOT exist — repo uses private helpers instead)
```

**Test Convention**: No pytest fixtures or conftest.py; private `_helper()` functions in each test file; `_require_database(dsn)` pattern for skip-if-unreachable.

---

## Running the Stack Locally

### Prerequisites
```bash
python -m uv sync --all-groups --frozen   # Install dev + production dependencies

# For integration tests (optional):
cd deploy/development
docker compose up -d postgres minio otel-collector prometheus
# Wait ~10s for services to be healthy
```

### Commands

**Unit tests only** (no external services):
```bash
.venv/Scripts/python.exe -m pytest tests/unit -q
```

**All tests** (integration tests skip-if-unreachable):
```bash
.venv/Scripts/python.exe -m pytest -q
```

**Linting & Type Checking**:
```bash
.venv/Scripts/python.exe -m ruff check src tests       # 0 issues expected
.venv/Scripts/python.exe -m pyright --pythonpath .venv/Scripts/python.exe src tests   # 0 errors expected
```

**Coverage Report**:
```bash
.venv/Scripts/python.exe -m pytest --cov=production_optimizer --cov-report=term-missing
# Must pass: fail_under=90 branch coverage (adapters/* omitted)
```

**Run Migrations**:
```bash
export OPTIMIZER_DATABASE_DSN="postgresql://optimizer:change-me@localhost:5432/optimizer"
.venv/Scripts/python.exe scripts/migrate.py
```

**Run Lane A for real, end to end, against any repository**:
```bash
.venv/Scripts/python.exe scripts/optimize.py <path-to-repo> --objective "Fix the failing tests"
# e.g. .venv/Scripts/python.exe scripts/optimize.py fixtures/sample-repo --objective "Fix the failing tests"
```
Picks a real model provider from whichever of `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/
`GEMINI_API_KEY`/`DEEPSEEK_API_KEY` is set in `.env`, else a reachable local
Ollama server, else a clearly-labeled `LocalScriptedModelProvider` stand-in (the
pipeline still runs for free, just without real analysis). Runs A1→A2→A3→C0
and prints the sealed `ConvergenceDecision`/`ConvergedCase`. Lane B has no
equivalent CLI yet — see `docs/implementation/04-orchestration-frame-as-built.md`.

---

## Key Design Patterns & Invariants

### 1. Digest Linkage (Not Nesting)
```python
# ✅ CORRECT: cross-artifact reference via content digest
class FindingSet(ArtifactEnvelope):
    evidence_bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

# ❌ WRONG: don't nest full EvidenceBundle object
class FindingSet(ArtifactEnvelope):
    evidence_bundle: EvidenceBundle  # NO — breaks sealed-artifact immutability
```

### 2. Idempotency Key Derivation
- Default: `f"{case_id}:{node_id}:{idempotency_key_version}"`
- Override in node-specific handler if needed (e.g., "request_digest + source_digest")
- **Never** use timestamps or random UUIDs in the key — must be deterministic

### 3. Tenant Isolation
- ALL adapters accept `tenant_id` as a parameter to every public method
- `IntentLedger.get(tenant_id=..., idempotency_key=...)` — composite key, not just idempotency_key alone
- `PostgresIntentLedger` uses `(tenant_id, idempotency_key)` as composite PK

### 4. Side Effect Classification
```python
class SideEffectClass(StrEnum):
    PURE = "pure"                      # Always recomputed, never cached
    READ_ONLY = "read_only"            # Cached, safe to replay
    IDEMPOTENT_WRITE = "idempotent_write"  # Cached with idempotency lookup
    EXTERNAL_JOB = "external_job"      # Delegated to WorkerBroker
```
- `PURE` nodes: `NodeRuntime.execute()` skips intent lookup, always calls handler
- Non-`PURE` nodes: intent `prepare()` → `get()` for cache hit → reuse OR `execute()` → `complete()`

### 5. Failure Modes & Recovery
| Scenario | Recovery | Test |
|----------|----------|------|
| Crash after `prepare()` but before handler call | `get()` sees `pending`, retry handler | (natural retry in outer loop) |
| Crash after handler but before `complete()` | `get()` sees `pending`, handler runs again (idempotent output expected) | `test_control_plane_restart.py` |
| Crash after `complete()` but before checkpoint | `get()` sees `completed`, replay result from artifact | `test_control_plane_restart.py` |
| Unknown state (handler partially failed) | `mark_unknown()` + reconciliation signal | (future external reconciler) |

---

## Known Limitations & Deferred Work

### P1 FRAME-0
- ✅ PostgreSQL control plane (case/intent/outbox)
- ✅ S3 artifact store (content-addressed, tenant-isolated)
- ✅ Python deterministic policy (fail-closed, golden-decision tested)
- ✅ JWT identity (bearer token extraction + RBAC)
- ✅ OTel telemetry (span attributes, no secrets)
- ✅ Local worker broker (thread pool; every real script uses this, not Kubernetes)
- ✅ Kubernetes worker broker adapter (implemented, unit-tested) — unwired and never run against a live cluster

### P2 Contracts
- ✅ A1–C0 sealed artifacts (150+ Pydantic models)
- ✅ Canonical JSON + digest verification (wired into exports)
- ✅ JSON Schema generation (per-model `.model_json_schema()`, `schemas/`)
- ✅ Node manifest (99 task IDs → input/output contract bindings)

### P3 Business Handlers — all 99 nodes, both lanes
- ✅ A1, A2, A3 (Lane A): real, tested, runnable end to end via `scripts/optimize.py`
- ✅ B1, B2 (Lane B): real, tested handler logic; B1.95/B2.22 mount the same real A2/A3 subgraphs
- ✅ C0 (shared convergence): real, tested; used by both lanes
- ⏳ B1.32-35 real query adapters (metrics/logs/traces/LLM-execution-evidence) — deliberate scope decision, not started
- ⏳ Lane B scheduler/CLI trigger — no cron/webhook/watcher calls `build_lane_b_discovery_graph` anywhere in this repo
- ⏳ Differentiated C0 failure routing (producer-stage / A3-B2 / close-case per failure type) — today one `rejected` outcome, reasons recorded in the artifact
- ⏳ A real benchmark/performance-metric collector for arbitrary criteria — only command-exit-code-based evidence (`unit_command_result`, `lint_command_result`, ...) is reliably collected today
- ⏳ `LocalWorkerBroker` does not cancel/kill the underlying subprocess when a job times out

### Known Deferred Work
- **Kubernetes worker broker**: adapter code + unit tests exist; live cluster acceptance evidence deferred
- **OPA policy adapter** (HTTP POST to OPA): Python deterministic policy is primary; OPA deferred
- **Async/concurrency**: Current adapters are sync; async refactor deferred
- **Observability**: telemetry emitted but OpenTelemetry collector plumbing not fully validated in prod
- **Public API/MCP surface**: does not exist; `scripts/optimize.py` is the only real Lane A entrypoint
- **Database connection pooling**: Single pool per adapter; no shared pool for cost efficiency (future optimization)

---

## Environment Variables & Configuration

### Required for Runtime
```
OPTIMIZER_ENVIRONMENT=development|test|production
OPTIMIZER_LOG_LEVEL=INFO|DEBUG|WARNING
OPTIMIZER_POLICY_VERSION=bootstrap-v1
OPTIMIZER_DATABASE_DSN=postgresql://user:pass@host:5432/db
OPTIMIZER_ARTIFACT_ENDPOINT=http://localhost:9000
OPTIMIZER_ARTIFACT_BUCKET=optimizer-development
OPTIMIZER_POLICY_URL=http://localhost:8181    # (not used by Python policy; for OPA HTTP future)
OPTIMIZER_OTEL_ENDPOINT=http://localhost:4317
```

### Development Defaults (.env.example)
- S3: `optimizer` / `change-me-now` (MinIO)
- Postgres: `optimizer` / `change-me` @ localhost:5432
- Policy version: `bootstrap-v1`

### Secrets Storage
- **Credential retrieval**: `SecretsBroker.lease(tenant_id, secret_ref, purpose, ttl_seconds)` (context manager)
- **Current implementations**: `S3ArtifactStore` reads credentials from env @ construction time (not via SecretsBroker)
- **Never store credentials in**: checkpoint state, telemetry events, artifact metadata

---

## Next Steps for Future Work

Node manifest/JSON Schema generation and all 99 business-node handlers
(A1–C0, both lanes) are done — see `docs/implementation/04-orchestration-frame-as-built.md`
and `docs/project-blueprint/10-current-gap-analysis.md` for the current,
authoritative gap list. What's actually left:

### Lane B trigger surface
1. Decide the real trigger (cron, webhook, or a long-running watcher) that
   should call `build_lane_b_discovery_graph`/`build_lane_b_proposal_graph`.
2. Add a script or service entrypoint analogous to `scripts/optimize.py`.
3. Wire B1.32-35's real query adapters once the relevant observability
   infrastructure (metrics/logs/traces store, LLM execution log) exists.

### Kubernetes Worker Broker rollout
1. `KubernetesWorkerBroker` itself is implemented and unit-tested
   (`adapters/production/kubernetes_worker_broker.py`) — remaining work is
   validating it against a live cluster and wiring it into a script/runtime
   as an alternative to `LocalWorkerBroker`.

### OPA & Observability
1. Implement `OpaHttpPolicy` adapter (HTTP POST to OPA running `policies/bootstrap.rego`)
2. Validate telemetry pipeline end-to-end (OTLP → collector → Prometheus/Grafana)
3. Add audit logging for all policy decisions

### C0 failure routing
1. If the convergence doc's differentiated routing (digest mismatch → producer
   stage; missing eligible solution → A3/B2; obsolete source → close/version)
   is still wanted, it requires new conditional edges in
   `orchestration/subgraphs/c0.py` and cross-graph routing back into Lane A/B,
   not just handler logic.

---

## Troubleshooting

### "PostgreSQL is not reachable" (Integration Tests Skip)
**Expected behavior** — integration tests skip if `OPTIMIZER_DATABASE_DSN` is unreachable.
```bash
docker compose -f deploy/development/compose.yaml up -d postgres
# Wait ~5s for service to be healthy
.venv/Scripts/python.exe -m pytest tests/integration -v   # Should show SKIPPED → PASSED
```

### Pyright "reportMissingImports" for psycopg/boto3
**Root cause**: psycopg/boto3 have weak stubs; environment quirk.
**Solution**: Use `--pythonpath .venv/Scripts/python.exe` flag:
```bash
.venv/Scripts/python.exe -m pyright --pythonpath .venv/Scripts/python.exe src tests
```

### Coverage Below 90%
**Possible causes**:
- New adapter code not tested (adapters/* is omitted, so untouched adapters won't break the gate)
- Business logic in a tested module has a branch without a test case

**Check**:
```bash
.venv/Scripts/python.exe -m pytest --cov=production_optimizer --cov-report=term-missing --cov-report=html
# Open htmlcov/index.html in browser
```

### "artifact_type must be frozen" or "schema_version mismatch"
**Likely**: Manual mutation of a sealed artifact after construction.
**Solution**: Use `.model_copy(update={...})` to create new instance with updates.
```python
# ❌ WRONG
artifact.artifact_type = "NewType"  # Raises FrozenInstanceError

# ✅ RIGHT
updated = artifact.model_copy(update={"coverage_gaps": [...]})
```

---

## Useful One-Liners

```bash
# Run only a single test
.venv/Scripts/python.exe -m pytest tests/unit/test_node_runtime.py::test_runtime_rejects_undeclared_route -v

# Run tests matching a pattern
.venv/Scripts/python.exe -m pytest -k "idempotent" -v

# Show print() output during test run
.venv/Scripts/python.exe -m pytest -s tests/unit/test_contracts_a3.py

# Measure test runtime
.venv/Scripts/python.exe -m pytest --durations=10

# Generate coverage HTML report
.venv/Scripts/python.exe -m pytest --cov=production_optimizer --cov-report=html && start htmlcov/index.html
```

---

## References

- **Architecture**: `/docs/project-blueprint/` (lane-a, lane-b, shared-workflow, data-contracts, two-lane-delivery-roadmap)
- **LangGraph**: `src/production_optimizer/orchestration/` (root graph, subgraph builders, node routing)
- **Type Checking**: pyproject.toml `[tool.pyright]` section
- **Migrations**: `migrations/001_*.sql`, `migrations/002_*.sql`, `scripts/migrate.py`

