# 02. Plan and Task List

## Objective

Translate one selected solution strategy into ordered, independently verifiable
experiment phases.
The plan must identify exact source targets, commands, acceptance criteria,
measurement and rollback before implementation begins.

## Stage Contract

- Owner: engineering automation team.
- Inputs: selected strategy, repository manifest, A2 protocol and sandbox policy.
- Outputs: `ExecutionPlan@1.0`, `TaskList@1.0`, and `PlanQualityReport@1.0`.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S02.10 | Resolve selected treatment | Revalidate source/config/prompt targets against pinned snapshot | Selection/manifest | Resolution report | Tree-sitter, Semgrep, CodeQL [SRC-TREE] [SRC-SEMGREP] [SRC-CODEQL] have language/coverage limits |
| S02.20 | Discover dependencies | Identify modules, owners, tests, build commands, services and migration constraints | Manifest/treatment | Dependency map | Joern/CodeQL assist graphs [SRC-JOERN] [SRC-CODEQL]; dynamic behavior remains uncertain |
| S02.30 | Draft implementation strategy | Coding-capable LLM proposes minimal change sequence from supplied evidence | Context | Plan draft | LLM is useful for drafting, but cannot invent files/commands or approve itself |
| S02.40 | Materialize experiment phases | Refine approved strategy templates into diagnostic or implementation phases, ordered by dependency and risk; each phase has one logical treatment | Draft/risk | Phase list | LangGraph subgraph controls iteration; policy validates boundaries |
| S02.50 | Generate tasks | Define objective, files/symbols, dependencies, instructions, owner and status per task | Phases | Task list | Pydantic validates structure [SRC-PYDANTIC], not feasibility |
| S02.60 | Define done criteria | Bind every phase to exact tests, metrics, thresholds, sample protocol and guardrails | A1/A2/tasks | Acceptance matrix | k6/Bencher support measurements [SRC-K6] [SRC-BENCHER], comparability remains custom |
| S02.70 | Define rollback | State restoration command/patch, trigger, deadline and responsible actor | Phase/treatment | Rollback plan | Git supports code restoration [SRC-GIT]; data migrations need domain-specific mechanisms |
| S02.80 | Critic review | Separate model checks omissions, assumptions, blast radius and operational effects | Plan draft/evidence | Critique | Model diversity reduces shared bias but does not replace deterministic gates |
| S02.81 | Deterministic validation | Verify all paths/symbols/commands, one-treatment rule, complete criteria coverage and dependency DAG | Revised plan | `PlanQualityReport` | OPA [SRC-OPA] plus repository resolver |
| S02.90 | Approve and seal | Interrupt when required; bind approval to plan digest and active phase | Valid plan | `ExecutionPlan@1.0`, `TaskList@1.0` | LangGraph interrupt/checkpoint [SRC-LG-INTERRUPT] [SRC-LG-PERSIST] |

## Rules

- `BR-02-001`: A strategy may have multiple phases; each phase changes one logical independent variable, though it may touch multiple files.
- `BR-02-006`: Diagnostic phases may test supported hypotheses but cannot authorize production implementation or rollout.
- `BR-02-002`: Every command must be discovered or explicitly approved against the pinned snapshot.
- `BR-02-003`: Every phase defines build/test, measurement, done conditions and rollback.
- `BR-02-004`: Tasks form an acyclic dependency graph.
- `BR-02-005`: LLM-generated plans remain invalid until repository and policy gates pass.

Definition of done: the plan is executable in order, each phase is isolated and
reversible, all A1 criteria are covered, and approval is digest-bound.
