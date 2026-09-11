# C0. Lane Convergence

## Objective

C0 removes lane-specific differences and proves that downstream processing has
one complete, internally consistent case. It prevents Lane B automation or a
manual Lane A shortcut from bypassing evidence gates.

## Stage Contract

- Owner: control-plane team.
- Inputs: Lane A handoff or Lane B proposal envelope and canonical artifact references.
- Outputs: `ConvergenceDecision@1.0` and `ConvergedCase@1.0` envelope.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| C0.10 | Identify origin | Accept only `manual` or `automatic`; retain origin for audit but not downstream quality policy | Lane package | Origin decision | Pydantic validates shape [SRC-PYDANTIC], not truth |
| C0.20 | Validate artifacts | Validate request, baseline, findings, solutions and quality schemas/versions | A1-A3 or B1-B2 package | Schema report | Pydantic/JSON Schema [SRC-PYDANTIC] [SRC-JSON-SCHEMA] need migration readers for old versions |
| C0.30 | Verify digest chain | Confirm request, source snapshot, evidence and solution references form one immutable chain | Artifact refs | Integrity report | Object lock/versioning protects storage [SRC-MINIO-LOCK], while platform code verifies relationships |
| C0.40 | Verify semantic equivalence | Require B1 request/baseline to satisfy A1/A2 contracts and B2 to satisfy A3 policy | Valid artifacts | Equivalence report | Platform-owned deterministic rules; no framework knows business equivalence |
| C0.50 | Recheck freshness | Reject or refresh stale source, evidence, approval or ownership before expensive work | Time/source identities | Freshness decision | Git [SRC-GIT] supplies source identity, not business expiry |
| C0.60 | Apply convergence gate | Require comparable baseline, passed A3 quality and at least one eligible solution | Reports | `ConvergenceDecision` | OPA can evaluate policy [SRC-OPA]; policy quality remains company-owned |
| C0.70 | Publish common case | Produce lane-neutral references and handoff event for step 01 | Passed package | `ConvergedCase@1.0` envelope | LangGraph persistence [SRC-LG-PERSIST] records transition, not raw artifacts |

## Rules and Exceptions

- `BR-C0-001`: A lane origin cannot change after case creation.
- `BR-C0-002`: Request, baseline and solutions must reference the same source snapshot.
- `BR-C0-003`: At least one solution is eligible; high score cannot override hard failure.
- `BR-C0-004`: Stale or mismatched artifacts fail closed.
- Digest mismatch routes to the producer stage; missing eligible solutions route
  to A3/B2; an obsolete source closes or versions the case.

Definition of done: `ConvergedCase@1.0` is complete, fresh, digest-valid and
accepted by deterministic policy.
