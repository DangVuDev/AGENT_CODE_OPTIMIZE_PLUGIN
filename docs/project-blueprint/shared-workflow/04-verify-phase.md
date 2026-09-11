# 04. Verify One Phase

## Objective

Prove that the active patch builds, preserves required behavior and satisfies
security/static/domain checks before any performance claim is evaluated.

## Stage Contract

- Owner: quality engineering team.
- Inputs: patch artifact, baseline verification state and verification manifest.
- Outputs: `VerificationReport@1.0` and verification decision.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S04.10 | Verify patch identity | Confirm patch/base/phase digest and clean reproducible workspace | Patch/plan | Verification intake | Git [SRC-GIT] and artifact digests |
| S04.20 | Resolve verification manifest | Select mandatory build, lint, type, unit, integration, security and domain checks | A2 manifest/phase | Command plan | Repository-native tools are authoritative; generated commands need approval |
| S04.30 | Build and static checks | Execute compiler, lint/type checks and analyzers with pinned versions | Workspace | Check results | Semgrep/CodeQL/Slither [SRC-SEMGREP] [SRC-CODEQL] [SRC-SLITHER] have false positives/coverage limits |
| S04.40 | Run existing tests | Execute affected and required full suites; preserve raw output and flakes | Workspace | Test results | Testcontainers provides disposable dependencies [SRC-TESTCONTAINERS], not test adequacy |
| S04.50 | Run regression/domain tests | Validate business invariants, contracts, compatibility, migrations and security properties | Guardrails | Guardrail results | Foundry supports Solidity traces/tests [SRC-FOUNDRY] |
| S04.60 | Evaluate generated tests | Add tests only to expose changed behavior; prevent tautological tests and preserve existing suites | Change/evidence | Supplemental tests | Coding agents may draft; independent review/gates required |
| S04.70 | Classify failures | Distinguish patch regression, baseline-existing failure, flaky test, environment failure and tool failure | Current/A2 baseline | Failure attribution | Deterministic comparison plus bounded human review |
| S04.80 | Apply verification gate | Require all mandatory checks and guardrails; exceptions need policy approval | Results/policy | `VerificationDecision` | OPA [SRC-OPA] |
| S04.90 | Seal report | Store commands, outputs, versions, durations, coverage and decision | Results | `VerificationReport@1.0` | Artifact storage preserves raw evidence |

## Rules

- `BR-04-001`: Verification runs immediately after each phase.
- `BR-04-002`: Existing tests are never replaced by generated tests.
- `BR-04-003`: Baseline failures and newly introduced failures are reported separately.
- `BR-04-004`: Mandatory guardrail failure returns only the active phase to step 03.
- `BR-04-005`: Performance improvement cannot excuse correctness/security failure.

Definition of done: mandatory checks pass or the patch is routed back with exact
failure evidence; every command and raw result is reproducible.
