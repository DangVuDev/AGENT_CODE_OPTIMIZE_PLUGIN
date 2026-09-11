# 01. Product Vision

## Problem

The platform accepts a codebase and an optimization objective, collects real
evidence, verifies bottlenecks, proposes multiple solutions, selects a treatment,
creates a plan, implements one phase at a time, tests it, measures it again,
decides whether to keep, revise, or revert it, reports the outcome, and rolls it
out under controlled conditions.

There are two entry paths:

- Lane A: a person explicitly identifies the feature and objective.
- Lane B: the system detects a bottleneck from production history.

Both lanes converge on the same downstream process after solutions exist. The
automatic lane must not have a weaker or shorter path.

## Core Value

The product's value is not a newly invented coding agent. Its value is a control
plane that can prove:

1. The correct feature and commit were measured before any change.
2. Every root-cause claim and solution is linked to traceable evidence.
3. Each experiment changes exactly one logical variable.
4. Before-and-after tests and measurements are comparable.
5. Decisions are based on policy and measurements, not LLM confidence.
6. Every change is reversible and auditable.

## Non-Negotiable Invariants

```text
MEASURE_BEFORE_CHANGE
VERIFY_BEFORE_TRUST
ONE_LOGICAL_VARIABLE_PER_EXPERIMENT
CHEAP_AND_REVERSIBLE_BEFORE_EXPENSIVE
TEST_EVERY_PHASE
DECIDE_FROM_MEASUREMENT
ROLLBACK_MUST_BE_EXECUTABLE
```

Violating an invariant is a hard failure. An LLM cannot override it.

## Scope

Included in the target platform:

- Local codebases under the initial `local_analysis` profile. Remote Git and
  deployment integration belong to the later `connected_production` profile.
- Runtime services, LLM pipelines, prompt/RAG systems, configuration, Go,
  Python, TypeScript/JavaScript, Java, Solidity, and polyglot repositories.
- Telemetry, profiling, benchmarking, testing, static analysis, and repository
  intelligence.
- Policy-controlled automation and human approval at risk boundaries.
- Patch and PR creation, controlled measurement, reporting, and progressive
  rollout when the `connected_production` profile is enabled.

The canonical scope profiles and requirements are defined in
[00-requirements-and-decisions.md](00-requirements-and-decisions.md).

Excluded from the control plane:

- Replacing the company's observability backends.
- Inventing a quality metric when no dataset or scorer exists.
- Allowing an agent to execute directly on a production host.
- Automatically rolling out a high-risk change without policy and a kill switch.

## Production-Ready Definition

A workflow is production-ready only when all of the following are true:

- LangGraph uses durable checkpoints and resumes after process failure.
- Artifacts have schema versions, digests, provenance, and retention policies.
- Collectors ingest real data and fail closed when evidence is missing or
  incomparable.
- Sandboxes isolate source, secrets, network access, and resources.
- Every node has an idempotency key, timeout, retry policy, and dead-letter path.
- Approval is bound to an artifact digest and an authenticated identity.
- Measurement has statistical policies and executable guardrails.
- Rollback has been tested rather than merely documented.
- Audit records can reconstruct every decision from immutable artifacts.

## Product-Level Success Metrics

| Category | Candidate metrics |
|---|---|
| Reliability | Workflow completion rate, resume success rate, duplicate side-effect rate |
| Evidence | Comparable-baseline rate, evidence freshness, provenance coverage |
| A3/B2 | Grounded-finding precision, eligible-solution rate, reviewer rejection rate |
| Execution | Patch success rate, test pass rate, sandbox violations, median retries |
| Optimization | Accepted improvements, guardrail regressions, revert rate |
| Operations | Mean time to recover, queue latency, artifact retrieval success |
| Cost | LLM tokens per case, compute minutes per case, cost per accepted improvement |
