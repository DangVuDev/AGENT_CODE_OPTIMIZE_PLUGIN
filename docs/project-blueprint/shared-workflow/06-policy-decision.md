# 06. Policy Decision

## Objective

Convert verified test and measurement evidence into a deterministic operational
decision: KEEP, FIX_ONE_PART or REVERT. An LLM may explain the outcome but may
not decide it.

## Stage Contract

- Owner: decision-policy team; rollback owner depends on target type.
- Inputs: A1 criteria, verification, comparable measurement, active phase and policy.
- Outputs: `Decision@1.0` and, when required, `RollbackReport@1.0`.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S06.10 | Validate decision evidence | Require passed verification, comparable measurement and complete criteria | Reports | Decision intake | Pydantic/OPA [SRC-PYDANTIC] [SRC-OPA] |
| S06.20 | Evaluate primary targets | Apply A1 operators, tolerances and practical significance | Measurement/A1 | Target results | Deterministic policy |
| S06.30 | Evaluate guardrails | Fail closed on required correctness, security, quality, cost or reliability regression | Reports/A1 | Guardrail results | OPA [SRC-OPA] |
| S06.40 | Attribute phase result | Identify whether one repairable phase/component caused failure or the treatment direction lacks evidence | Provenance/results | Attribution report | LLM may summarize, but evidence/policy determine classification |
| S06.50 | Decide | KEEP when targets/guards pass; FIX_ONE_PART for localized repairable deficiency; REVERT for wrong direction/no evidence/guard failure | Evaluations | `Decision/v1` | Deterministic Python or OPA |
| S06.60 | Execute required rollback | For REVERT restore base state and verify restoration; FIX_ONE_PART preserves only policy-approved workspace state | Decision/rollback plan | Rollback evidence | Git [SRC-GIT] handles source; other state needs domain adapters |
| S06.70 | Route graph | KEEP→07, FIX_ONE_PART→03 active phase, REVERT→01 after rollback | Decision | Route event | LangGraph conditional edges [SRC-LG-OVERVIEW] |
| S06.80 | Explain and seal | Render facts, failed criteria and rationale; sign policy version and evidence digests | Decision | Decision package | LLM explanation cannot mutate structured decision |

## Decision Table

| Verification | Comparability | Primary targets | Guardrails | Decision |
|---|---|---|---|---|
| Fail | Any | Any | Any | FIX_ONE_PART or REVERT by attribution policy |
| Pass | Fail | Unknown | Unknown | No decision; return to 05/A2 |
| Pass | Pass | Pass | Pass | KEEP |
| Pass | Pass | Partial, localized and repairable | Pass | FIX_ONE_PART |
| Pass | Pass | Fail/no improvement | Pass | REVERT |
| Pass | Pass | Any | Fail | REVERT |

Definition of done: a deterministic decision is sealed and any mandatory
rollback has been executed and verified before routing.
