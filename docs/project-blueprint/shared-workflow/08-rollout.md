# 08. Progressive Rollout

## Objective

Deliver a KEEP treatment through risk-ordered exposure stages while observing
the same primary metrics and guardrails and preserving a tested rollback path.
For a local-only tool, this stage requires an explicit deployment adapter; it
must not pretend that modifying a local working tree is production rollout.

## Stage Contract

- Owner: release engineering and service owner.
- Inputs: KEEP decision, deployable artifact, connected-production profile, rollout and rollback plans.
- Outputs: `RolloutReport@1.0` and deployment/rollback evidence.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S08.10 | Authorize release | Verify KEEP, report, target environment, owner, window and deployment permission | Decision/report | Release authorization | OPA [SRC-OPA] plus deployment identity/RBAC |
| S08.20 | Build rollout plan | Define shadow/internal/canary/gradual/full stages, cohort, duration and promotion thresholds | Risk/metrics | `RolloutPlan` | Argo Rollouts supports canary/blue-green [SRC-ARGO], not business policy |
| S08.30 | Configure exposure | Create versioned feature flags or traffic routing with default-safe state | Plan | Exposure config | OpenFeature API [SRC-OPENFEATURE] needs a provider and governance |
| S08.40 | Validate rollback readiness | Exercise rollback/kill switch, recovery time and data compatibility before exposure | Plan/artifacts | Rollback readiness | Argo can automate rollback [SRC-ARGO]; irreversible migrations need bespoke strategy |
| S08.50 | Deploy stage | Promote one cohort using immutable artifact and record deployment identity | Approved stage | Deployment event | Argo/Kubernetes [SRC-ARGO] [SRC-K8S-JOB] |
| S08.60 | Observe window | Query primary metrics and guardrails for the declared window; preserve raw observations | Deployment/queries | Stage observations | Prometheus/Loki/Tempo [SRC-PROM] [SRC-LOKI] [SRC-TEMPO] |
| S08.70 | Decide promotion | Promote, hold or roll back using deterministic thresholds; high-risk promotion may interrupt | Observations/policy | Stage decision | OPA and LangGraph interrupt [SRC-OPA] [SRC-LG-INTERRUPT] |
| S08.80 | Execute rollback | Restore previous version/exposure, verify health and create incident evidence | Failure decision | Rollback report | Argo/OpenFeature assist mechanisms, not causal analysis |
| S08.90 | Complete and monitor | Reach full exposure, continue post-rollout window, close case or create new Lane B signal | Final observations | `RolloutReport@1.0` | LangGraph controls lifecycle [SRC-LG-PERSIST] |

## Rules

- `BR-08-001`: Only KEEP decisions are rollout-eligible.
- `BR-08-002`: Promotion changes one exposure stage at a time.
- `BR-08-003`: Guardrail failure triggers automatic rollback when policy says so.
- `BR-08-004`: Every stage uses immutable deployment and query identities.
- `BR-08-005`: Full exposure is not completion until the final observation window passes.
- `BR-08-006`: A new bottleneck starts a new Lane A/B case; it does not rewrite this case.

Definition of done: full rollout and observation pass, or rollback is executed
and verified; every stage, metric, approval and deployment is auditable.
