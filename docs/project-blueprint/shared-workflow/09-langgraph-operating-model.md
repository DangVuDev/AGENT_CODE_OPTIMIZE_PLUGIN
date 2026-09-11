# Shared Workflow LangGraph Operating Model

## Graph Ownership

```text
SharedWorkflowSubgraph
  C0 -> 01 -> 02 -> PhaseLoop
                    PhaseLoop: 03 -> 04 -> 05 -> 06
                               ^     |         |
                               |fail |         |FIX_ONE_PART
                               +-----+---------+
  06 KEEP -> 07 -> 08 -> complete
  06 REVERT -> rollback -> 01
```

Every numbered step is a subgraph. Each side effect, policy gate and approval is
a distinct node so checkpoint/resume does not repeat untracked actions
[SRC-LG-PERSIST].

## Shared State Additions

```python
class SharedWorkflowState(TypedDict, total=False):
    converged_case_ref: ArtifactRef
    ranking_ref: ArtifactRef
    selected_solution_ref: ArtifactRef
    plan_ref: ArtifactRef
    active_phase_id: str
    active_task_ids: list[str]
    workspace_lease_ref: ArtifactRef
    patch_ref: ArtifactRef
    verification_ref: ArtifactRef
    measurement_ref: ArtifactRef
    decision_ref: ArtifactRef
    report_ref: ArtifactRef
    rollout_ref: ArtifactRef
    pending_interrupt: InterruptEnvelope | None
    iteration_count: int
    budget_usage_ref: ArtifactRef
```

## Loop Controls

| Loop | Entry condition | Exit condition | Protection |
|---|---|---|---|
| Plan revision | Invalid or rejected plan | Quality/approval pass | Revision count and model budget |
| Implementation repair | Verification fail or FIX_ONE_PART | Verification and remeasurement pass | Active phase only; clean workspace |
| Measurement retry | Incomparable transient run | Comparable report | Fixed protocol and retry cap |
| Solution fallback | REVERT | Different approved solution | Prior solution/treatment exclusion |
| Rollout stage | Prior stage passes | Full observation completes | Max hold time and rollback trigger |

## Approval Boundaries

Potential interrupts occur at solution selection, plan approval, dangerous tool
authorization, policy exceptions and rollout promotions. Each payload contains
case/stage, artifact digest, decision options, actor role, expiry and concise
evidence links. Resuming an interrupt re-enters the node from its beginning, so
pre-interrupt side effects must be idempotent [SRC-LG-INTERRUPT].

## Production Controls

- PostgreSQL checkpointer and durable object storage [SRC-LG-POSTGRES]
  [SRC-MINIO-VERSIONING].
- Transactional idempotency ledger for external jobs and deployments.
- Leased isolated workspaces with deny-by-default network and short-lived secrets.
- Versioned schemas, policies, prompts, tools, images and metric transforms.
- Per-node timeouts, retry taxonomy, cost/resource budgets and cancellation.
- End-to-end OpenTelemetry traces with case/node/artifact identities [SRC-OTEL].
- Compensating action for every state-changing node.
- Reconciliation workers for jobs that finish after graph-worker failure.

## Cross-Step Acceptance

The shared workflow is production-ready when a case can resume after failure at
every node; duplicate callbacks do not duplicate patches, tests, measurements
or deployments; all loops are bounded; every decision is evidence-based and
replayable; agent permissions remain phase-scoped; and KEEP, FIX_ONE_PART and
REVERT produce the exact documented route and artifacts.
