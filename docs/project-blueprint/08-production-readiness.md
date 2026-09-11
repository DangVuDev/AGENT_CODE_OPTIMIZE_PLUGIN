# 08. Production Readiness

## Reliability

- Use a PostgreSQL LangGraph checkpointer; SQLite is for local development only
  [SRC-LG-PERSIST].
- Make every node idempotent and persist side-effect intent before execution.
- Apply exponential backoff with jitter only to transient failures.
- Use queue leases, heartbeats, cancellation, and a dead-letter queue.
- Add circuit breakers for LLM, telemetry, Git, and deployment providers.
- Test backup, restore, and disaster recovery.

Initial SLOs:

| SLI | Initial target |
|---|---|
| Durability of accepted node transitions | >= 99.99% |
| Resume success after worker failure | >= 99.9% |
| Duplicate external side effects | 0 |
| Artifact digest verification | 100% |
| Rollback initiation after a guardrail breach | <= 2 minutes |

## Security

- Treat source, logs, issues, prompts, and model output as untrusted input.
- Use disposable, non-root workspaces with read-only base images and quotas.
- Deny network egress by default and allowlist destinations per phase.
- Issue short-lived, narrowly scoped secrets at the worker; never checkpoint them.
- Allowlist tools and validate arguments; avoid shell command strings.
- Compare the patch against approved files, symbols, and treatment scope.
- Encrypt and sign artifacts, isolate tenants, and enforce retention.
- Bind approvals to identity, RBAC, artifact digest, and expiration.
- Audit every tool call, policy decision, interrupt, and resume.

OWASP identifies excessive functionality, permissions, and autonomy as an LLM
agency risk. The control plane must constrain all three [SRC-OWASP-AGENCY].

## Control-Plane Observability

Every node emits a span containing:

```text
case_id, thread_id, node_id, attempt
tenant_id, feature_id, repository_id, commit_sha
input and output artifact digests
provider, tool, model, and version
latency, tokens, cost, and result code
interrupt_id and policy_version
```

Do not log source, evidence, or secrets directly. Log references and digests;
store raw content in encrypted object storage.

Required dashboards:

- Queue depth and node latency.
- Errors by node, provider, and repository language.
- Interrupt age and stale approvals.
- Evidence comparability and freshness.
- LLM invalid-output, retry, token, and cost rates.
- Sandbox timeouts and policy violations.
- KEEP, FIX_ONE_PART, REVERT, and rollout rollback rates.

## LLM Safety and Cost

- Complete A1 and A2 preflight before any LLM call.
- Put token and cost budgets in A1 and enforce them at the gateway.
- Require structured output, local validation, and bounded repair attempts.
- Separate generator and judge roles or models for high-risk cases.
- Treat source text as data and broker every tool call.
- Cache only by immutable digest, model, and policy version.
- Route provider failure to retry, circuit breaking, or manual fallback; never
  invent evidence.

## Sandbox

Kubernetes Jobs run bounded workloads to completion and support retries,
deadlines, and cleanup [SRC-K8S-JOB]. gVisor adds an application-kernel isolation
layer with compatibility and performance tradeoffs [SRC-GVISOR]. Minimum profile:

```text
runAsNonRoot
readOnlyRootFilesystem
drop all Linux capabilities
seccomp or runtimeClass
CPU, memory, and ephemeral-storage limits
activeDeadlineSeconds
ttlSecondsAfterFinished
default-deny network policy
short-lived service account and secret lease
```

Very high-risk workloads or software incompatible with gVisor may use stronger
microVM or Kata-class isolation according to policy.

## Release Gates

Do not release without:

- Schema migration and rollback procedures.
- Contract tests against exact adapter versions.
- Crash, resume, and idempotency tests.
- Tenant-isolation and authorization tests.
- Prompt-injection and tool-abuse tests.
- A/A benchmark noise characterization.
- A completed rollback drill.
- On-call runbooks, dashboards, and alerts.
- SBOM, dependency, license, and CVE review.
- Data retention, deletion, and incident-response ownership.

## Platform Rollout Stages

1. Shadow: read telemetry without producing visible proposals.
2. Suggest-only: execute A1-A3 and B1-B2 without code changes.
3. Plan-only: stop after step 02 for human review.
4. Sandbox patch: execute 03-06 without automatically creating a PR.
5. PR automation: create a PR only after KEEP.
6. Canary rollout: execute step 08 with explicit approval.
7. Policy-based autonomy for low-risk configuration changes after sufficient
   production evidence exists.

