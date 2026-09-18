# Production Optimizer

An evidence-grounded control plane for automated code optimization, built on
LangGraph. It takes a repository and a measurable goal ("get p95 latency of the
tags endpoint under 5 ms"), measures a real baseline, proposes grounded
strategies, plans and implements a change in an isolated workspace, verifies and
re-measures it, and either keeps, repairs or reverts it — recording every step as
a sealed, digest-linked artifact.

The defining constraint: **nothing is fabricated**. Every number resolves to a
real command that really ran. When something cannot be measured, the system says
so rather than guessing.

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture at a glance](#architecture-at-a-glance)
- [Quick start](#quick-start)
- [Running the pipeline](#running-the-pipeline)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [Development workflow](#development-workflow)
- [Guide for AI agents](#guide-for-ai-agents)
- [Known limitations](#known-limitations)

---

## What it does

The workflow runs in two lanes that converge on a shared decision gate:

**Lane A (manual)** — a human asks for an optimization.

| Stage | Nodes | What it does |
|---|---|---|
| **A1** Requirement Intake | 14 | Parse the request into a sealed `OptimizationRequest` |
| **A2** Real Baseline | 18 | Run real commands (build/lint/test/benchmark) and seal a `BaselineSnapshot` |
| **A3** Grounded Solutions | 22 | Analyze evidence, produce findings and a `SolutionPortfolio` |

**Lane B (automatic)** — the system discovers opportunities itself.

| Stage | Nodes | What it does |
|---|---|---|
| **B1** Discovery | 26 | Scan registered sources for optimization signals |
| **B2** Proposal | 12 | Qualify a signal into a proposal (reuses A2/A3 internally) |

**C0** (7 nodes) is the shared convergence gate: schema validation, digest-chain
verification, equivalence and freshness checks.

**Shared workflow (S01–S07)** — 64 nodes that take a converged case to a result.

| Stage | Nodes | What it does |
|---|---|---|
| **S01** Rank & Select | 9 | Score strategies, pick a winner, halt for approval if risky |
| **S02** Plan & Task List | 10 | Draft phases and tasks, critic review, deterministic validation |
| **S03** Implement | 9 | Apply one change in an isolated workspace copy |
| **S04** Verify | 9 | Run the repository's own checks against the patch |
| **S05** Remeasure | 9 | Re-run the workload and compare against baseline |
| **S06** Decide | 9 | KEEP / FIX_ONE_PART / REVERT / ESCALATE |
| **S07** Report | 9 | Publish a `OptimizationReport` (JSON + Markdown) |

S03→S04→S05→S06 form a bounded repair loop (max 5 attempts per phase). A
verification failure or a `FIX_ONE_PART` decision sends the case back to S03; a
`REVERT` sends it back to S01 with the failed strategy excluded.

**163 business nodes total** (99 in lanes A/B + C0, 64 in the shared workflow),
all with real production handlers.

---

## Architecture at a glance

```
                 ┌──────────────┐
  manual ───────▶│  A1 → A2 → A3│──┐
                 └──────────────┘  │   ┌────┐
                                   ├──▶│ C0 │──▶ S01 → S02 ──▶ ┌─────────────┐
                 ┌──────────────┐  │   └────┘                  │ S03→S04→S05 │
  discovery ────▶│  B1  →   B2  │──┘                           │      ↕      │
                 └──────────────┘                              │     S06     │
                                                               └──────┬──────┘
                                                        KEEP ─────────┴──▶ S07
```

### Core concepts

**Sealed artifacts.** Every stage output is an immutable Pydantic model
(`ArtifactEnvelope`) with a content digest. Artifacts reference each other by
digest (`*_digest: str`), never by nesting — so tampering is detectable and the
chain is verifiable end to end at C0 and S07.10.

**Node runtime.** Every business node runs through `NodeRuntime.execute()`,
which handles intent idempotency, artifact lifecycle and telemetry uniformly.
Handlers have one signature: `(state, ports) -> NodeExecution`.

**Ports and adapters.** Fourteen protocol definitions (`ports/`) with production
implementations (`adapters/production/`): PostgreSQL for case/intent/outbox,
S3/MinIO for artifacts, JWT identity, OpenTelemetry, deterministic Python policy,
local and Kubernetes worker brokers, and five LLM providers (Anthropic, OpenAI,
Gemini, DeepSeek, Ollama) behind one `GenericModelProvider`.

**Fail-closed by default.** A node with no registered handler stops the run. A
policy decision defaults to deny. A model that returns unusable output does not
get silently papered over.

---

## Quick start

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies
python -m uv sync --all-groups --frozen

# 2. Run the test suite (no external services needed — 412 tests)
.venv/Scripts/python.exe -m pytest tests/unit tests/contract -q

# 3. Configure a model provider
cp .env.example .env
#    then set at least one of ANTHROPIC_API_KEY / OPENAI_API_KEY /
#    GEMINI_API_KEY / DEEPSEEK_API_KEY, or run a local Ollama server.

# 4. Run the pipeline against a repository
.venv/Scripts/python.exe scripts/run.py lane1 fixtures/go-checkout \
    --feature-id checkout \
    --metric unit_command_result --direction minimize --target 0 \
    --unit exit_code --unit-command "go test ./..."
```

On Linux/macOS use `.venv/bin/python` instead of `.venv/Scripts/python.exe`.

Without any API key the run still completes using `LocalScriptedModelProvider`,
a clearly-labeled deterministic stand-in — the pipeline exercises end to end for
free, just without real analysis.

---

## Running the pipeline

`scripts/run.py` is the single entrypoint. Pick how much of the workflow to run
with a tag:

| Tag | Runs | Use it to |
|---|---|---|
| `lane1` | A1 → A2 → A3 → C0 | Measure a baseline and get strategies, change nothing |
| `lane1-plan` | ... → S01 → S02 | Also produce a reviewable plan, touch no workspace |
| `full` | ... → S03 → S07 | Implement, verify, remeasure, decide and report |
| `lane2` | B1 only | Automatic discovery scan |

### A minimal run

```bash
.venv/Scripts/python.exe scripts/run.py lane1 /path/to/repo \
    --feature-id checkout \
    --metric unit_command_result --direction minimize --target 0 \
    --unit exit_code \
    --unit-command "pytest"
```

### A full run with a Docker Compose evaluation

When the metric needs a running application, use the `docker_compose` execution
profile so A2 measures the real workload rather than just a command exit code:

```bash
.venv/Scripts/python.exe scripts/run.py full /path/to/repo \
    --feature-id tags \
    --metric p95_latency_ms --direction minimize --target 5 --unit ms \
    --guardrail-metric-id correctness \
    --workload-id tags-http --environment-id docker-node-lts \
    --build-command "npm run build" \
    --lint-command "npm run lint" \
    --unit-command "npm test" \
    --execution-profile docker_compose \
    --compose-file docker-compose.yaml \
    --eval-id tags-http --eval-service app \
    --eval-command node scripts/evaluate-tags.mjs \
    --eval-metrics p95_latency_ms,correctness \
    --eval-repetitions 3 --eval-warmup 1 --eval-timeout 60 \
    --application-services app,db \
    --maximum-worker-seconds 300 \
    --model-provider gemini --model-id gemini-3.6-flash
```

The evaluation command must print metric values the platform can parse; see
`application/metrics/` for the supported shapes.

### Key flags

| Flag | Meaning |
|---|---|
| `--metric` / `--direction` / `--target` / `--unit` | The primary criterion |
| `--guardrail-metric-id` | Metric that must not regress (default: `unit_command_result`) |
| `--execution-profile` | `legacy_discovery` (command exit codes) or `docker_compose` |
| `--model-provider` / `--model-id` | `auto` (default), `anthropic`, `openai`, `gemini`, `deepseek`, `ollama`, `local-scripted` |
| `--trace-node-outputs` | Print every node's state update as JSON — the best debugging tool here |
| `--maximum-worker-seconds` | Per-command budget |

### Reading the output

A run prints one section per stage. The important lines:

- `PlanQualityReport.passed` — did S02's plan clear every gate?
- `node_routes (...)` — which route each node took (`continue` / `revision` /
  `rejected` / `approval`)
- `phase repair attempts` — how many S03→S04 repair passes were used (max 5)
- `Case did not reach a published OptimizationReport` — the case ended without a
  KEEP. This is a **valid business outcome**, not a crash: the change did not
  meet its criteria within budget.

Exit code 0 means the workflow ran correctly, whatever it decided. A traceback
means a real defect.

---

## Configuration

All configuration is environment variables, loaded from `.env` at the repo root.

### Control plane

`OPTIMIZER_CONTROL_PLANE` selects the posture:

| Value | Behavior |
|---|---|
| `auto` (default) | Use each durable adapter that is configured *and* answering; otherwise fall back loudly, per port |
| `durable` | Require them — a misconfigured dependency raises instead of degrading |
| `memory` | In-process everything, no connection attempts |

Each run prints what it actually got:

```
[control-plane] NOT durable
[control-plane]   intents: in-memory (set OPTIMIZER_DATABASE_DSN for a durable ledger)
[control-plane]   artifacts: in-memory (set OPTIMIZER_ARTIFACT_* for a durable store)
```

Never assume durability from the presence of a DSN — read this block.

### Variables

| Variable | Purpose |
|---|---|
| `OPTIMIZER_DATABASE_DSN` | PostgreSQL for intents + LangGraph checkpoints |
| `OPTIMIZER_ARTIFACT_ENDPOINT` / `_BUCKET` / `_ACCESS_KEY` / `_SECRET_KEY` | S3/MinIO artifact store |
| `OPTIMIZER_OTEL_ENDPOINT` | OpenTelemetry collector |
| `OPTIMIZER_POLICY_VERSION` | Policy version stamped into decisions (default `bootstrap-v1`) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY` | Model credentials |
| `OLLAMA_BASE_URL` | Local model server (default `http://localhost:11434/v1`) |
| `OPTIMIZER_MODEL_PROVIDER_ORDER` | Provider preference for `--model-provider auto` |

### Local infrastructure

```bash
cd deploy/development
docker compose up -d postgres minio otel-collector prometheus

export OPTIMIZER_DATABASE_DSN="postgresql://optimizer:change-me@localhost:5432/optimizer"
.venv/Scripts/python.exe scripts/migrate.py    # idempotent
```

---

## Repository layout

```
src/production_optimizer/
├── contracts/          Sealed Pydantic artifacts (a1…c0, s01…s07) + state + digests
├── ports/              10 protocol definitions (artifacts, intents, policy, model…)
├── adapters/production/  Postgres, S3, JWT, OTel, workers, LLM providers
├── application/        Business node handlers — one package or module per stage
│   ├── node_runtime.py     Universal execution: idempotency → handler → artifact
│   ├── resume.py           Human-in-the-loop interrupt/resume
│   ├── s03_agent_loop.py   The bounded, tool-constrained implement agent
│   └── {a1,a2,a3,b1,b2,c0,s01,s02}_handlers/  (+ s03…s07_handlers.py)
└── orchestration/      LangGraph wiring: subgraphs, lanes, shared workflow, catalog

scripts/run.py          The entrypoint (tags: lane1, lane1-plan, full, lane2)
scripts/migrate.py      Database migrations
docs/project-blueprint/ The authority — code follows these, not the reverse
tests/{unit,contract,integration,acceptance}/
```

### Where the LLM is actually used

Only four nodes call a model. Everything else is deterministic:

| Node | Role | File |
|---|---|---|
| A1.x, A2.31, A3.40/50/60 | intake, command proposal, findings, strategies | `a1/a2/a3_handlers/shared.py` |
| **S02.30** | Draft phases and tasks | `s02_handlers/nodes/s02_30_*.py` |
| **S02.80** | Independent critic review | `s02_handlers/nodes/s02_80_*.py` |
| **S03.50** | Implement agent (read/write/run tools) | `s03_agent_loop.py` |
| **S07.70** | Report narrative | `s07_handlers.py` |

S01, S04, S05 and S06 contain **no model calls at all** — ranking, verification,
remeasurement and the policy decision are fully deterministic by design.

---

## Development workflow

```bash
# Tests (unit + contract need no external services)
.venv/Scripts/python.exe -m pytest tests/unit tests/contract -q

# Integration tests skip cleanly when Postgres/MinIO are unreachable
.venv/Scripts/python.exe -m pytest tests/integration -v

# Lint and types
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m pyright --pythonpath .venv/Scripts/python.exe src tests

# Coverage (gate: 90% branch, adapters/* omitted)
.venv/Scripts/python.exe -m pytest --cov=production_optimizer --cov-report=term-missing
```

### Test conventions

No `conftest.py` and no fixtures — each test file defines private `_helper()`
functions. Integration tests use a `_require_database(dsn)` skip-if-unreachable
pattern. Contract tests drive real handlers against in-memory ports.

Scripted model providers in tests are **grounded in the prompt they are given**:
they parse the real context (paths, criteria, treatments) rather than inventing
values, so a prompt change that breaks the contract breaks the test too. That is
intentional — keep it when editing prompts.

---

## Guide for AI agents

This section is for coding agents working in this repository.

### Read this first

1. **`docs/project-blueprint/` is the authority.** Every stage has a document
   defining its contract and business rules (`BR-xx-yyy`). When code and doc
   disagree, the doc wins. `shared-workflow/09-langgraph-operating-model.md`
   holds the cross-cutting rules.
2. **`CLAUDE.md`** has project-specific handbook notes, but parts of it lag
   behind the code. Verify against the source before relying on a claim.
3. Run `scripts/run.py <tag> --help` for the real, current CLI surface.

### The rules that matter most

**Never fabricate evidence.** This is the project's core invariant. If a check
did not run, report that it did not run — do not synthesize a passing result. If
a metric is unavailable, set `unavailable_reason`. A sealed artifact that
contradicts its own verdict is a bug, and has been one here before.

**Artifacts are immutable.** Use `.model_copy(update={...})`; never mutate a
sealed artifact. Cross-reference by `*_digest`, never by nesting the object.

**Idempotency keys are deterministic.** Never put a timestamp or random UUID in
one. Model-call keys must include the prompt version — `tests/unit/
test_prompt_version_discipline.py` enforces this, because a key without it
replays a cached completion from a different prompt, silently undoing your
prompt change.

**Prompts must state the rules their gates enforce.** The single most common
defect class in this codebase: a deterministic gate validates something the
generating prompt never mentioned, so the model fails a rule it was never told.
If you add or tighten a gate, update the prompt in the same change. If you edit
a prompt, bump its `_PROMPT_VERSION`.

### Subgraph state hazard

S03/S04/S05/S06 are compiled subgraphs added as **nodes** of the outer shared
workflow, and the outer graph genuinely revisits them on a repair pass. A
compiled subgraph returns its **absolute final state** for every shared field,
not a delta. Therefore:

> **Do not give `OptimizationState` a summing (`operator.add`) reducer for any
> field a revisited subgraph also writes.** The parent applies the reducer again
> to a value that already includes its own contribution, silently doubling it.

This caused a real production bug (`s03_revision_attempts` reaching `pass2`
after one failure). Revision counters are plain last-value fields; each writer
computes the new total itself. `tests/contract/test_resume_case.py` guards it.

### Making changes safely

- Run `pytest tests/unit tests/contract -q` before and after. 412 tests, ~2 min
  (440 including `integration` and `acceptance`).
- For prompt or handler changes, also run the real pipeline:
  `scripts/run.py lane1-plan <repo> ... --trace-node-outputs`
- `--trace-node-outputs` prints every node's state update — use it to find where
  a value first goes wrong, rather than reasoning backwards from a traceback.
- A new business node needs: a handler, a `NodeSpec` registration, a manifest
  entry, an entry in `orchestration/catalog.py`, and a contract test.

### Debugging checklist

| Symptom | Likely cause |
|---|---|
| `missing required X produced by <stage>-passN` | A pass-scoped artifact id mismatch — check how N was derived on both the write and read side |
| `artifact reference conflict for T:id` | Two different contents sealed under one artifact id — usually a missing pass scope |
| `node route conflict for N` | One node returned two different routes in a run without a distinct route key |
| Model change appears to do nothing | Idempotency key missing the prompt version → cached replay |
| Integration test skipped | Expected: Postgres/MinIO unreachable |
| `429 RESOURCE_EXHAUSTED` | Provider rate limit, not a code defect — wait or switch provider |

---

## Known limitations

These are deliberate scope decisions, documented honestly rather than hidden:

- **Lane B has no scheduler.** B1/B2 handlers are real and tested, but nothing
  triggers `build_lane_b_discovery_graph` on a schedule. `lane2` runs a scan
  manually.
- **B1.32–35 have no query adapters.** Historical metrics/logs/traces queries
  report `unavailable_reason` rather than inventing signals, so a real scan over
  an arbitrary repository usually detects nothing.
- **Kubernetes worker broker is unwired.** Implemented and unit-tested, never
  run against a live cluster. `LocalWorkerBroker` is what every script uses.
- **C0 failure routing is undifferentiated.** One `rejected` outcome today;
  reasons are recorded in `ConvergenceDecision.reasons`.
- **`LocalWorkerBroker` does not kill a timed-out subprocess.**
- **No public API or MCP surface.** `scripts/run.py` is the only entrypoint.
- **OPA policy adapter deferred.** The deterministic Python policy is primary.

---

## Further reading

| Document | Covers |
|---|---|
| `docs/project-blueprint/02-end-to-end-workflow.md` | The whole workflow narrative |
| `docs/project-blueprint/03-business-nodes.md` | Every node's contract |
| `docs/project-blueprint/06-data-contracts.md` | Artifact schemas |
| `docs/project-blueprint/shared-workflow/` | One document per S-stage, with business rules |
| `docs/project-blueprint/10-current-gap-analysis.md` | Authoritative gap list |
| `docs/implementation/` | Build order and as-built notes |
