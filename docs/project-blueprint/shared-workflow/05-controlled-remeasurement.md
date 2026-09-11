# 05. Controlled Remeasurement

## Objective

Measure the verified treatment against A2 while changing exactly one logical
variable and holding all material dimensions constant. Step 05 determines
whether a before/after comparison is valid before interpreting improvement.

## Stage Contract

- Owner: performance/evaluation engineering team.
- Inputs: A2 baseline/protocol, verified patch, treatment and environment manifest.
- Outputs: `Measurement@1.0`, `StatisticalReport@1.0`, `IsolationReport@1.0`, and `ComparabilityReport@1.0`.
- Operating details: [../12-production-operating-contracts.md](../12-production-operating-contracts.md).

## Task Catalogue

| ID | Task | Processing | Inputs | Outputs | Framework fit and limitation |
|---|---|---|---|---|---|
| S05.10 | Revalidate protocol | Pin A2 workload, samples, warmup, aggregation, environment and outlier rules | Baseline/phase | Experiment protocol | Platform comparability logic is authoritative |
| S05.20 | Audit treatment isolation | Confirm approved patch is the only logical difference; detect dependency/environment drift | Baseline/patch | Isolation report | Git diff [SRC-GIT] plus environment manifests |
| S05.30 | Prepare environment | Restore dataset, hardware profile, concurrency, cache and service dependencies | Protocol | Treatment environment | Containers/Testcontainers [SRC-TESTCONTAINERS] cannot eliminate host variance |
| S05.40 | Execute workload | Run warmups and repetitions; randomize/interleave A/B where required | Environment | Raw treatment samples | k6/Foundry/native benchmarks [SRC-K6] [SRC-FOUNDRY] |
| S05.50 | Preserve observations | Store raw output before parsing and bind sample IDs, time, tool and snapshot | Samples | Evidence refs | Immutable storage [SRC-MINIO-VERSIONING] |
| S05.60 | Validate sample quality | Check count, errors, noise, outliers and missing dimensions using predeclared rules | Raw samples | Quality report | Bencher can track regression [SRC-BENCHER], not define valid business comparisons |
| S05.70 | Compare dimensions | Verify feature, workload, dataset, model, environment, hardware, concurrency and cache state | A2/treatment evidence | `ComparabilityReport` | Deterministic platform validator |
| S05.80 | Calculate effects | Compute absolute/relative change, uncertainty and practical threshold for every criterion | Comparable samples | `StatisticalReport` | Statistical packages assist; business significance remains A1 policy |
| S05.90 | Seal measurement | Bind baseline, treatment, protocol, raw data and reports | Passed comparison | `Measurement/v1` | LangGraph records handoff [SRC-LG-PERSIST] |

## Rules

- `BR-05-001`: Only the approved logical treatment may differ from baseline.
- `BR-05-002`: Raw samples are retained; aggregates alone are insufficient.
- `BR-05-003`: Outlier and stopping rules are fixed before seeing treatment results.
- `BR-05-004`: Incomparable measurements cannot support KEEP or REVERT.
- `BR-05-005`: Every A1 primary criterion and guardrail is evaluated.

Incomparability routes to environment correction/retry; a fundamentally invalid
A2 baseline returns to A2. Definition of done: comparable measurements and
effect reports exist for all required metrics.
