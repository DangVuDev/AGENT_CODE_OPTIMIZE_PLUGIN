# 12. Production Operating Contracts

## Purpose

This is the canonical operating contract required by `REQ-OPS-001`. Detailed
business task documents define what nodes do; this matrix defines how each stage
must behave under production execution. Values are initial targets and require
load-test calibration before release.

## Stage Contracts

| Stage | Accountable owner | Preconditions | Idempotency key | Initial execution SLO | Retry | Terminal errors | Runbook outcome |
|---|---|---|---|---|---|---|---|
| A1 | Product workflow owner | Authenticated host identity, readable allowed local path, request payload | tenant + canonical input digest + policy version | p95 < 30 s excluding human wait | Provider transient errors only, max 2 repairs | INPUT_INVALID, SOURCE_INVALID, POLICY_DENIED | Clarify, reject, or publish immutable request |
| A2 | Evidence platform owner | Approved request, source access, evidence contract | request digest + source content digest + collector-plan digest | Repository-dependent; deadline declared in A1 budget | Transient collector/job failures with backoff | NO_DATA, SOURCE_CHANGED, INCOMPARABLE, SANDBOX_FAILED | Recollect, interrupt for evidence, or return to A1 |
| A3 | Optimization analysis owner | Comparable A2 artifacts and readable snapshot | evidence digest + analyzer/model/prompt/policy versions | p95 < 20 min within A1 budget | One structured-output repair plus approved fallback | NO_ACTIONABLE_PROBLEM, MODEL_OUTPUT_INVALID, NO_ELIGIBLE_STRATEGY | Request targeted evidence, revise failed candidates, or close |
| B1 | Discovery service owner | Registered repository/owner/query policies and frozen window | tenant + window + discovery-policy version | Complete within scan interval; no overlap | Transient read failures until scan deadline | NO_DATA, SOURCE_UNRESOLVED, OWNER_UNRESOLVED | No-op, suppress, quarantine, merge, or qualify |
| B2 | Proposal service owner | Fresh qualified opportunity and A3 authorization | opportunity digest + A3 policy/model versions | p95 < 20 min excluding approval | Same bounded A3 retry | STALE_OPPORTUNITY, NO_ELIGIBLE_STRATEGY | Refresh, request evidence, route review, or close |
| C0 | Control-plane owner | Complete lane handoff | lane package digest + convergence-policy version | p95 < 2 s | None for deterministic failures | ARTIFACT_MISMATCH, STALE_CASE, NO_ELIGIBLE_STRATEGY | Return to exact producer stage |
| 01 | Decision-policy owner | Converged case with eligible strategies | portfolio digest + ranking-policy version | p95 < 2 s excluding human wait | None; recompute on new inputs only | NO_SELECTABLE_STRATEGY, APPROVAL_EXPIRED | Select, revise, reject, or interrupt |
| 02 | Engineering automation owner | Selected strategy and current snapshot | selected digest + planner/prompt/policy versions | p95 < 10 min excluding approval | One bounded draft-critic revision cycle | PLAN_INVALID, COMMAND_UNRESOLVED, SCOPE_UNRESOLVED | Revise strategy/plan or approve and seal |
| 03 | Execution platform owner | Approved active phase and sandbox authorization | phase digest + base snapshot + executor version | Phase-specific deadline; hard kill required | Transient worker retry from clean workspace | SANDBOX_FAILED, EXECUTOR_FAILED, SCOPE_VIOLATION | Clean workspace, revise phase, or package patch |
| 04 | Quality engineering owner | Scoped patch and verification manifest | patch digest + verification-manifest digest | Repository-specific deadline in phase | Infrastructure/flaky-policy retry only | BUILD_FAILED, TEST_FAILED, GUARDRAIL_FAILED | Return active phase to 03 or pass to 05 |
| 05 | Performance engineering owner | Passed verification and frozen A2 protocol | patch digest + protocol + environment digest | Experiment budget from A1 | Transient noisy/infrastructure retries within cap | INCOMPARABLE, SAMPLE_INVALID, BUDGET_EXHAUSTED | Correct environment, return to A2, or publish measurement |
| 06 | Decision-policy owner | Passed verification and comparable measurement | measurement digest + decision-policy version | p95 < 2 s plus rollback SLO | None for decision; rollback adapter may retry safely | DECISION_INPUT_INVALID, ROLLBACK_FAILED | KEEP, FIX_ONE_PART, or verified REVERT route |
| 07 | Audit/reporting owner | Complete artifact chain and decision | decision digest + report-template version | p95 < 60 s | Storage/publisher transient retry | AUDIT_CHAIN_INVALID, REPORT_INCOMPLETE | Repair references or publish JSON/Markdown |
| 08 | Release engineering owner | KEEP, deployable artifact, connected-production profile | artifact + environment + rollout-plan digest | Promotion/rollback SLO in rollout plan | Deployment reconciliation, never blind duplicate promotion | DEPLOYMENT_FAILED, GUARDRAIL_BREACH, ROLLBACK_FAILED | Hold, promote, rollback, escalate, or close |

## Universal Node Contract

Every implementation node or subgraph declares:

```text
node_id and owner
input/output Pydantic models and supported schema versions
preconditions and postconditions
idempotency key derivation
side-effect class and compensating action
timeout, cancellation and budget
retryable and non-retryable error codes
artifact writes and audit event
metrics/logs/traces without raw secrets/source
conditional route table
runbook URL and escalation owner
```

## Error Taxonomy

| Class | Retry? | Routing rule |
|---|---|---|
| `*_INVALID`, `POLICY_DENIED`, `INCOMPARABLE` | No blind retry | Clarification, revision or rejection |
| `SOURCE_CHANGED`, `STALE_*` | No | Create new version or refresh producer stage |
| `*_UNAVAILABLE`, worker/network timeout | Bounded when transient | Retry with jitter and same idempotency key |
| `MODEL_OUTPUT_INVALID` | One repair/fallback | Preserve attempts; fail visibly at budget limit |
| `SCOPE_VIOLATION`, `GUARDRAIL_FAILED` | No | Reject patch or execute rollback route |
| `ROLLBACK_FAILED` | Controlled reconciliation | Escalate as critical incident; do not mark reverted |
| Unknown internal error | At most one safe replay | Dead-letter with complete diagnostic references |

## Approval Contract

Every approval includes `approval_id`, case/stage, actor and role, decision,
artifact digest, policy version, issued/expiry timestamps and rationale. Resume
must use the same LangGraph `thread_id`. A Boolean or numeric display rank is
not a valid approval.

## SLO Graduation

Initial targets are not production commitments until M7 load/chaos tests measure
their distributions. Promotion requires dashboards, alerts, a named on-call
owner, a linked runbook, error-budget policy, backup/restore evidence and at
least one successful failure drill for every state-changing stage.
