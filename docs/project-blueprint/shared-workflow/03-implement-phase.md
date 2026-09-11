# 03. Implement One Phase

## Objective

Produce one bounded patch for the active approved phase in an isolated working
copy. Step 03 changes source or configuration but makes no claim that the change
is correct or beneficial.

## Stage Contract

- Owner: isolated-execution platform team; repository owner owns treatment intent.
- Inputs: approved active phase, source snapshot, tool authorization and budget.
- Outputs: `PatchArtifact@1.0`, `ExecutionProvenance@1.0`, and scope report.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S03.10 | Authorize phase | Verify plan/phase digest, approval, budget, source snapshot and tool policy | Plan/phase | Execution authorization | OPA [SRC-OPA] requires external identity/secrets controls |
| S03.20 | Create isolated workspace | Materialize pinned snapshot, disposable branch/worktree, resource/network limits | Snapshot | Workspace lease | Kubernetes Job/gVisor [SRC-K8S-JOB] [SRC-GVISOR] add isolation with compatibility cost |
| S03.30 | Select executor | Route config to typed editor/Optuna, prompt to DSPy, bounded performance to Codeflash, code to approved agent | Treatment | Executor decision | Optuna/DSPy/Codeflash/agents [SRC-OPTUNA] [SRC-DSPY] [SRC-CODEFLASH] [SRC-OPENHANDS] do not own acceptance |
| S03.40 | Prepare least-privilege context | Supply only phase files, evidence, commands and short-lived secret references | Authorization | Tool context | Broker and sandbox are platform-owned |
| S03.50 | Execute treatment | Apply one approved logical change; record tool calls, commands, model/provider and costs | Context | Candidate workspace | Claude/Codex/OpenHands may implement [SRC-CLAUDE] [SRC-CODEX] [SRC-OPENHANDS]; behavior is nondeterministic |
| S03.60 | Enforce scope | Compare changed paths/symbols/dependencies and treatment semantics with plan | Candidate diff | Scope report | Git diff [SRC-GIT] plus AST resolvers; generated files require explicit policy |
| S03.70 | Sanitize output | Detect secrets, forbidden binaries, unexpected lockfile/migration changes and license issues | Diff/workspace | Sanitation report | Static/security tools assist but need company rules |
| S03.80 | Package patch | Record base snapshot, diff, changed symbols, transcript, executor/tool/image versions and digest | Passed diff | `PatchArtifact@1.0` | Immutable storage [SRC-MINIO-LOCK] preserves provenance |
| S03.90 | Update task state | Mark only implemented tasks ready for verification; never mark measured/complete | Patch/tasks | Updated task list | LangGraph owns status transition [SRC-LG-PERSIST] |

## Rules and Failure Routes

- `BR-03-001`: One invocation implements one phase only.
- `BR-03-002`: No command, network access or secret use outside authorization.
- `BR-03-003`: Out-of-scope changes reject the patch; they are not silently trimmed.
- `BR-03-004`: The implementing agent cannot approve its own output.
- Executor timeout/crash may retry from a clean workspace using idempotency.
- Scope or policy failure returns to plan revision or implementation; it never
  advances to measurement.

Definition of done: a scoped, sanitized and provenance-complete patch artifact
exists for exactly one phase.
