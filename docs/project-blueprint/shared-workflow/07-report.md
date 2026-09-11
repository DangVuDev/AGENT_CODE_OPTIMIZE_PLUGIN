# 07. Audited Report

## Objective

Create a human-readable and machine-readable record that independently explains
what was requested, measured, attempted, accepted, simplified, reverted or left
unfinished. Documentation is updated throughout the workflow; step 07 assembles
and validates it.

## Stage Contract

- Owner: audit/reporting team; case owner approves external publication.
- Inputs: complete artifact chain, decision, costs and event chronology.
- Outputs: `OptimizationReport@1.0` in machine-readable and Markdown forms.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S07.10 | Verify artifact chain | Validate every digest and producer/policy version from intake through decision | Case artifacts | Audit integrity report | Immutable storage [SRC-MINIO-LOCK] plus platform verifier |
| S07.20 | Reconstruct chronology | Order nodes, approvals, retries, costs and decisions from events/checkpoints | Events/state | Timeline | LangGraph persistence [SRC-LG-PERSIST] |
| S07.30 | Classify outcomes | Separate completed, simplified-with-reason, reverted and missing work | Tasks/decision | Outcome ledger | Deterministic status rules prevent narrative concealment |
| S07.40 | Summarize evidence | Present baseline/treatment metrics, uncertainty, comparability and guardrails with raw links | Measurements | Evidence summary | Visualization/template layer; no recomputation without versioned transform |
| S07.50 | Record source change | Include base revision, patch, files/symbols, tests and rollback state | Patch/verification | Change summary | Git [SRC-GIT] |
| S07.60 | Calculate cost and impact | Aggregate model, compute, analyzer, storage and engineering time where available | Usage records | Cost/impact summary | OTel/MLflow may provide usage [SRC-OTEL] [SRC-MLFLOW]; missing costs remain explicit |
| S07.70 | Generate narratives | Produce executive and engineering summaries only from structured facts | Verified package | Markdown report | LLM may draft; deterministic citation and omission checks remain required |
| S07.80 | Publish artifacts | Store JSON, Markdown and optional PR/dashboard links with access control | Reports | `OptimizationReport@1.0` | Object storage provides retention, not access governance |
| S07.90 | Apply report gate | Require all outcome categories, evidence links, limitations and rollback status | Report | Publication decision | OPA [SRC-OPA] |

## Rules

- `BR-07-001`: Missing evidence and failed work remain visible.
- `BR-07-002`: Report numbers resolve to raw evidence and transformation versions.
- `BR-07-003`: “Simplified” always includes reason and approver.
- `BR-07-004`: REVERT reports both attempted change and verified restoration.
- `BR-07-005`: Report generation never changes the workflow decision.

Definition of done: JSON and Markdown reports pass integrity/completeness gates,
are access-controlled and reconstruct the case without model transcripts.
