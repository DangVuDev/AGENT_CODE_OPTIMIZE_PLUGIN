# 03. Business Analysis by Node

## A1 - Manual Requirement Intake

Purpose: convert a human or agent request into a measurable optimization
contract.

Inputs:

- Source repository and a branch or revision resolvable to a commit SHA.
- `feature_id` and file, module, or service scope.
- Objective, priority, and budget.
- Primary criteria: latency, tokens, cost, gas, throughput, quality, or a domain
  metric.
- Guardrails: correctness, security, reliability, and quality.
- Workload, dataset, and environment identity.

Processing:

1. Normalize generic tags into typed criteria.
2. Resolve and pin the repository commit.
3. Validate metric units, directions, aggregations, targets, and query mappings.
4. Check the feature registry, RBAC, and conflicting requirements.
5. Generate an immutable request fingerprint.
6. Interrupt for missing information or approval.

Outputs: `OptimizationRequest@1.0` and `ApprovalArtifact@1.0`.

Gate: at least one primary criterion, executable guardrails, registered sources,
an authorized actor, and immutable content after approval.

Frameworks: Pydantic and JSON Schema for contracts; LangGraph interrupts and
checkpoints for clarification and approval. Pydantic does not replace policy or
RBAC.

## A2 - Real Baseline

Purpose: prove the pre-change state of the exact feature and commit.

Inputs: approved A1 request, telemetry query registry, repository credentials,
artifact key, and collector credentials.

Processing:

1. Collect historical logs, metrics, traces, profiles, evaluations, and
   benchmarks.
2. Build a repository manifest at the A1-pinned commit.
3. Normalize samples to common schemas and units.
4. Link each run or trace to feature, commit, workload, dataset, and source
   locations.
5. Store immutable raw bytes before parsing or aggregation.
6. Validate freshness, sample size, provenance, and sensitive-data policy.
7. Aggregate using the mean, percentile, rate, or other operator selected in A1.
8. Run the comparability gate.

Outputs: `BaselineSnapshot@1.0`, `EvidenceBundle@1.0`,
`RepositoryManifest@1.0`, and `ComparabilityReport@1.0`.

Gate: no invented data; every criterion has a source; dimensions match; enough
samples exist; provenance is traceable; and `comparable=true`.

Frameworks: OpenTelemetry, Loki, Prometheus, Tempo, Pyroscope, MLflow, native
benchmark tools, and Tree-sitter or language-specific indexers. The platform
must still define feature identity and comparability itself.

## A3 - Grounded Findings and Solutions

Purpose: identify problems, verify causes, and produce multiple treatments with
explicit tradeoffs.

Inputs: A1, A2, repository manifest, analyzer results, and profile or trace
evidence.

Processing:

1. Static and runtime analyzers generate observations.
2. An LLM proposes findings but may cite only supplied evidence IDs.
3. An independent evidence judge evaluates each exact claim.
4. Classify claims as `observation`, `hypothesis`, or `verified_cause`.
5. Generate materially different solution strategies. Strong hypotheses may
   produce diagnostic-only phases; implementation phases require verified
   causes or successful diagnostic evidence.
6. Require strategy-level tradeoffs and ordered phase templates; every phase
   has exactly one treatment, validation protocol, and rollback.
7. Deterministically reject negative-only evidence, missing files or symbols,
   unsupported causes, and out-of-scope treatments.

Outputs: `FindingSet@1.0`, `SolutionPortfolio@1.0`, and
`A3QualityReport@1.0`.

Gate: a verified finding cites both metric evidence and positive source or
runtime evidence; cause maturity controls diagnostic versus implementation
eligibility; candidate requirements pass; every A1 criterion is covered; and
rollback is executable.

Frameworks: Tree-sitter, Semgrep, CodeQL, Joern, Slither, Pyroscope, and an LLM.
No single tool proves root cause; evidence fusion, judging, and policy are still
required.

## B1 - Automatic Bottleneck Discovery

Purpose: create optimization cases from existing production runs.

Inputs: detection policy, metric registry, historical telemetry, ownership map,
and cooldown or deduplication state.

Processing:

1. A scheduler starts a LangGraph thread for a tenant and observation window.
2. Query history and group it by feature, workload, and environment.
3. Exclude stale or incomparable samples.
4. Detect SLO breaches, trend regressions, cost anomalies, and profile hotspots.
5. Score severity, confidence, impact, and strategic priority.
6. Deduplicate by feature, metric, and root fingerprint.
7. Assign an owner and create an A1-equivalent request plus an A2 baseline.

Outputs: `DetectionReport@1.0`, `OptimizationRequest@1.0`, and A2 artifacts.

Gate: use only existing real runs; do not launch a special benchmark for
discovery; require an owner, sufficient confidence, and a passed cooldown rule.

Frameworks: Prometheus, Loki, OpenTelemetry, Pyroscope, and MLflow. Anomaly
libraries may assist detection, but business thresholds and ownership remain
platform policy.

## B2 - Automatic Grounded Proposal

Purpose: apply A3-level quality controls while proactively routing a proposal to
the responsible owner.

Inputs: B1 opportunity, A1-equivalent request, and A2 evidence.

Processing: reuse A3 analyzers, generator, judge, and quality gate; add owner
routing, deduplication, cooldown, and an approval interrupt.

Outputs: `ProposalEnvelope@1.0`, `FindingSet@1.0`, and
`SolutionPortfolio@1.0`.

Gate: B2 must never be weaker than A3. The owner must approve risk above the
automatic policy threshold.

## C0 - Lane Convergence

Purpose: guarantee that downstream processing does not depend on the entry lane.

Inputs: signed A1 and A2 artifacts plus A3 or B2 output.

Processing: verify schema, signature, and fingerprint; validate request-baseline-
solution binding; require a comparable baseline, complete quality report, and at
least one eligible solution.

Output: `ConvergenceDecision@1.0`.

Gate: fail closed when artifacts disagree on request, commit, tenant, or
evidence digest.

## 01 - Re-rank and Select a Solution

Purpose: choose the treatment that best matches A1 rather than the solution that
sounds most persuasive.

Inputs: solution portfolio, criteria, weights, priority, risk policy, and approval
policy.

Processing:

1. Apply hard gates for security, migration, blast radius, evidence, and
   rollback.
2. Score criterion impact, confidence, reversibility, effort, and regression
   risk.
3. Prefer cheaper and reversible tiers: experiment/config, prompt, code, then
   architecture.
4. Auto-select only when policy permits; otherwise interrupt for a user choice.
5. Bind approval to the solution digest and ranking-policy version.

Outputs: `RankingResult@1.0`, `SelectedSolution@1.0`, and
`ApprovalArtifact@1.0`.

Frameworks: OPA may evaluate policy as code. Optuna must not be used as the
policy authority. Ranking and approval thresholds are platform-owned logic.

## 02 - Plan and Task List

Purpose: turn one selected solution strategy into independent, testable, and reversible
phases.

Inputs: selected strategy, repository manifest, build and test discovery,
sandbox policy, and ownership map.

Processing:

1. An LLM or coding agent drafts a plan.
2. Split phases by risk and dependency.
3. Enforce exactly one logical treatment per phase.
4. Resolve every file, symbol, and command against the pinned commit.
5. Assign acceptance criteria, done conditions, measurement, rollback, and
   approval requirements.
6. Run a plan critic.
7. Run deterministic scope, command, and criteria-coverage validation.

Outputs: `ExecutionPlan@1.0`, `TaskList@1.0`, and `PlanQualityReport@1.0`.

Frameworks: a LangGraph draft-critic-revise subgraph, repository intelligence,
and OPA. An LLM must not be the final validator.

## 03 - Implement One Phase

Purpose: produce a patch for one approved phase in an isolated workspace.

Inputs: approved phase, pinned commit, tool allowlist, short-lived secret lease,
and resource budget.

Processing:

1. Create a disposable workspace and branch.
2. Route the treatment to Optuna, DSPy, Codeflash, or a coding agent.
3. Permit the agent to read and modify only approved scope.
4. Broker every command, network request, and secret access.
5. Produce a patch and reject files or treatments outside scope.
6. Store transcripts, tool calls, image digest, and patch digest.

Outputs: `PatchArtifact@1.0` and `ExecutionProvenance@1.0`.

Frameworks: OpenHands, Claude Agent SDK, Codex, or SWE-agent; Kubernetes Job
with gVisor or stronger isolation. An agent cannot approve its own patch.

## 04 - Verify One Phase

Purpose: verify a patch immediately after its phase rather than deferring all
tests until the end.

Inputs: patch, verification manifest, and repository-owned commands.

Processing: build, unit and integration tests, regression tests, static and
security scans, linting, type checking, domain tests, flaky-test policy,
coverage or behavior comparison, and artifact capture.

Output: `VerificationReport@1.0`.

Gate: any required failure returns only the active phase to step 03. Generated
tests supplement but never replace existing tests.

Frameworks: pytest, `go test`, Jest, Foundry, Semgrep, CodeQL, Slither, and
Testcontainers as appropriate to the repository.

## 05 - Controlled Remeasurement

Purpose: measure the treatment using the A2 protocol while changing exactly one
logical variable.

Inputs: baseline, patch, experiment protocol, and controlled dimensions.

Processing: replay the workload; perform warmup; randomize or interleave when
needed; collect raw samples; apply the outlier policy; compare statistically;
then run the comparability validator.

Outputs: `Measurement@1.0`, `StatisticalReport@1.0`, and
`ComparabilityReport@1.0`.

Gate: feature, dataset, model, hardware, concurrency, cache state, and workload
must match. The approved treatment is the only permitted difference.

Frameworks: native benchmarks, k6, Bencher, and MLflow or DSPy evaluation. A
benchmark framework cannot independently prove comparability.

## 06 - Policy Decision

Purpose: derive a deterministic decision from verified measurement.

Inputs: A1 thresholds, measurement, verification report, and risk policy.

Rules:

- `KEEP`: primary targets pass and every guardrail passes.
- `FIX_ONE_PART`: evidence supports improvement, but one specific phase fails.
- `REVERT`: there is no improvement evidence or a guardrail fails.

Outputs: `Decision@1.0` and `RollbackReport@1.0` for REVERT.

Frameworks: OPA or deterministic Python policy. An LLM may explain the decision
but cannot be the authority.

## 07 - Audited Report

Purpose: produce an outcome that can be independently reconstructed.

Inputs: the complete artifact chain and decision.

Processing: verify the digest chain; separate `completed`,
`simplified_with_reason`, and `missing_or_reverted`; calculate impact and cost;
render JSON and Markdown; sign and publish.

Outputs: `OptimizationReport@1.0` and optional PR or dashboard updates.

Frameworks: a Markdown template renderer, object storage, and OpenTelemetry.
Reporting must not conceal missing evidence.

## 08 - Progressive Rollout

Purpose: deliver a KEEP treatment through risk-ordered production stages.

Inputs: optimization report, rollout policy, deployment manifest, guardrail
queries, and tested rollback.

Processing: shadow, internal allowlist, canary, gradual, and full rollout;
LangGraph interrupts at promotion gates; monitor every observation window;
automatically roll back guardrail failures; continue observation after full
rollout.

Outputs: `RolloutReport@1.0`, deployment and rollback evidence, and a closed case.

Frameworks: Argo Rollouts, OpenFeature, Prometheus, and Kubernetes. Argo owns the
delivery mechanism; LangGraph continues to own business state and approval
history.
