# 01. Re-rank and Select a Solution

## Objective

Select the solution strategy best aligned with the A1 objective, weighted criteria,
guardrails and risk appetite. Ranking supports a decision; it does not disguise
uncertainty or make ineligible solutions selectable.

## Stage Contract

- Owner: decision-policy team; accountable approver depends on risk policy.
- Inputs: converged case, solution portfolio, A1 criteria and policy versions.
- Outputs: `RankingResult@1.0`, `SelectedSolution@1.0`, and approval artifact.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S01.10 | Freeze decision context | Pin solution portfolio, criteria weights, policy, budget and actor roles | Converged case | `SelectionContext` | LangGraph checkpoint [SRC-LG-PERSIST] |
| S01.20 | Apply hard gates | Exclude strategies with invalid cause-maturity route, security/migration denial, missing rollback, scope or guardrail coverage | Strategies/policy | Eligibility report | OPA [SRC-OPA] cannot improve bad policy inputs |
| S01.30 | Normalize estimates | Put benefit, confidence, effort, reversibility and risk on registered scales; retain raw forecasts | Eligible strategies | Normalized factors | Deterministic platform logic; LLM estimates remain forecasts |
| S01.40 | Score against A1 | Calculate criterion impact, confidence, priority, effort and regression risk using a versioned formula | Factors/A1 | Score breakdown | Optuna is not a decision authority; use policy code |
| S01.50 | Apply risk ladder | Prefer experiment/config before prompt/code/architecture when benefits are materially comparable | Scores/risk | Ranked list | OPA can encode ordering [SRC-OPA]; applicability outranks artificial tier diversity |
| S01.60 | Analyze sensitivity | Show whether reasonable weight changes alter the winner and expose close calls | Ranking inputs | Sensitivity report | Custom deterministic analysis prevents false precision |
| S01.70 | Choose decision mode | Auto-select only below configured risk/uncertainty; otherwise request owner choice | Ranking/policy | Route decision | LangGraph interrupt [SRC-LG-INTERRUPT] is not identity verification |
| S01.80 | Capture selection | Approve, revise or reject; bind full strategy ID, never ambiguous display rank alone | Decision | `SelectedSolution@1.0` | Signed approval and RBAC complement interrupts |
| S01.90 | Seal selection | Persist score, alternatives, rationale, actor and digest | Accepted decision | `SelectedSolution@1.0`, ranking and approval artifacts | Immutable storage preserves history [SRC-MINIO-VERSIONING] |

## Rules

- `BR-01-001`: Ineligible strategies cannot be approved.
- `BR-01-002`: Numeric rank is display-only; decisions bind `solution_id` and digest.
- `BR-01-003`: Every score exposes its component values and policy version.
- `BR-01-004`: High-risk, low-confidence and close-call selections require human approval.
- `BR-01-005`: REVERT later returns here with prior evidence attached; it does not erase history.

Definition of done: exactly one eligible solution is selected, approval policy
passes, alternatives remain visible and the immutable selection package exists.
