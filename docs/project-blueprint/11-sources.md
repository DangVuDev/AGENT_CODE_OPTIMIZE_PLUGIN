# 11. Official Sources

Last verified: 2026-09-10. A framework source establishes only that a capability
exists. It does not prove that this project's deployment is production-ready.

## Orchestration and Contracts

| ID | Official source | Supported claim |
|---|---|---|
| SRC-LG-OVERVIEW | [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) | Low-level orchestration for long-running stateful workflows with deterministic and agentic nodes |
| SRC-LG-APP-STRUCTURE | [LangGraph application structure](https://docs.langchain.com/oss/python/langgraph/application-structure) | A deployable application declares dependencies, graph exports, environment configuration and optionally `langgraph.json`; application source remains project-owned |
| SRC-LG-PERSIST | [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Checkpointers persist thread state; production should use persistent storage such as PostgreSQL |
| SRC-LG-INTERRUPT | [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | Thread-bound HITL pause and resume; pre-interrupt effects must be idempotent because nodes restart |
| SRC-LG-POSTGRES | [LangGraph PostgreSQL checkpointer](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres) | PostgreSQL-backed checkpoint implementation; setup/migration and restricted deserialization configuration are deployment responsibilities |
| SRC-PYDANTIC | [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/) | Typed validation and JSON Schema generation |
| SRC-JSON-SCHEMA | [JSON Schema overview](https://json-schema.org/overview/what-is-jsonschema) | Declarative validation of JSON structure and constraints |
| SRC-OPA | [Open Policy Agent documentation](https://www.openpolicyagent.org/docs) | General-purpose policy as code with structured decisions |
| SRC-GIT | [Git documentation](https://git-scm.com/docs) | Local repository revision, status, diff, submodule, and object operations |

## Observability and Evidence

| ID | Official source | Supported claim |
|---|---|---|
| SRC-OTEL | [OpenTelemetry documentation](https://opentelemetry.io/docs/) | Instrumentation, generation, collection, and export of traces, metrics, and logs; not a storage backend |
| SRC-LOKI | [Grafana Loki documentation](https://grafana.com/docs/loki/latest/) | Log aggregation and LogQL queries |
| SRC-PROM | [Prometheus overview](https://prometheus.io/docs/introduction/overview/) | Labeled time series, PromQL, and alerting integrations |
| SRC-TEMPO | [Grafana Tempo documentation](https://grafana.com/docs/tempo/latest/set-up-for-tracing/) | Distributed trace storage, search, and signal correlation |
| SRC-PYROSCOPE | [Grafana Pyroscope documentation](https://grafana.com/docs/pyroscope/latest/) | Open-source continuous profiling and source-level correlation |
| SRC-MLFLOW | [MLflow GenAI tracing](https://mlflow.org/docs/latest/genai/tracing/) | LLM and agent tracing, latency and token observations, and OTel compatibility |
| SRC-MLFLOW-EVAL | [MLflow trace evaluation](https://www.mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/traces/) | Evaluation of stored production traces with built-in or custom scorers |

## Code Intelligence and Optimization

| ID | Official source | Supported claim |
|---|---|---|
| SRC-TREE | [Tree-sitter](https://tree-sitter.github.io/) | Incremental multi-language concrete syntax tree parsing |
| SRC-SEMGREP | [Semgrep local and CLI scans](https://semgrep.dev/docs/category/local-and-cli-scans) | Local static scans and custom rules |
| SRC-CODEQL | [GitHub CodeQL](https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-code-scanning) | Queryable code databases and vulnerability or coding-error queries |
| SRC-JOERN | [Joern code property graph](https://docs.joern.io/code-property-graph/) | Code property graphs for mining and querying source |
| SRC-SLITHER | [Slither repository](https://github.com/crytic/slither) | Solidity and Vyper static analysis and detector ecosystem |
| SRC-FOUNDRY | [Foundry traces](https://getfoundry.sh/forge/traces) | Forge test traces and gas information |
| SRC-OPTUNA | [Optuna optimization algorithms](https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/003_efficient_optimization_algorithms.html) | Sampling and pruning for optimization trials |
| SRC-DSPY | [DSPy documentation](https://dspy.ai/) | Optimization of LM programs against a user-defined metric |
| SRC-CODEFLASH | [How Codeflash works](https://docs.codeflash.ai/codeflash-concepts/how-codeflash-works) | Function discovery, candidate generation, testing, benchmarking, and pull-request workflows |
| SRC-BENCHER | [Bencher benchmarking](https://bencher.dev/docs/explanation/benchmarking/) | Benchmark history and threshold-based regression detection |
| SRC-K6 | [k6 thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/) | Pass/fail thresholds over load-test metrics |

## Coding Agents and Sandbox

| ID | Official source | Supported claim |
|---|---|---|
| SRC-OPENHANDS | [OpenHands Agent SDK](https://docs.openhands.dev/sdk/index) | Python and REST agent SDK with command, file, web, and MCP tools |
| SRC-OPENHANDS-SERVER | [OpenHands Agent Server](https://docs.openhands.dev/sdk/guides/agent-server/overview) | Remote isolated workspaces on containers, Kubernetes, VMs, or on-premises infrastructure |
| SRC-CLAUDE | [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) | Agent loop and built-in read, edit, and command tools |
| SRC-CLAUDE-HOSTING | [Claude Agent SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting) | Production hosting guidance for sandbox, resources, and network control |
| SRC-CODEX | [OpenAI Agents SDK tools](https://openai.github.io/openai-agents-python/tools/) | Experimental Codex tool for workspace-scoped shell, file, and MCP tasks with approval controls |
| SRC-SWEAGENT | [SWE-agent usage](https://swe-agent.com/latest/usage/cl_tutorial/) | Execution of issue or problem statements against local or GitHub repositories |
| SRC-K8S-JOB | [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/) | One-off Pods, retries, deadlines, parallelism, and cleanup |
| SRC-GVISOR | [gVisor documentation](https://gvisor.dev/docs/) | OCI-compatible application-kernel sandbox with compatibility and performance tradeoffs |
| SRC-TESTCONTAINERS | [Testcontainers Python](https://github.com/testcontainers/testcontainers-python) | Disposable container dependencies for integration tests |

## Artifacts and Rollout

| ID | Official source | Supported claim |
|---|---|---|
| SRC-MINIO-VERSIONING | [MinIO object versioning](https://min.io/docs/minio/kubernetes/upstream/administration/object-management/object-versioning.html) | Multiple object versions and recovery |
| SRC-MINIO-LOCK | [MinIO object locking](https://min.io/docs/minio/windows/administration/object-management/object-retention.html) | WORM retention for versioned objects |
| SRC-ARGO | [Argo Rollouts concepts](https://argo-rollouts.readthedocs.io/en/stable/concepts/) | Canary and blue-green delivery, metric analysis, promotion, and rollback |
| SRC-OPENFEATURE | [OpenFeature introduction](https://openfeature.dev/docs/reference/intro/) | Vendor-neutral feature-flag API |
| SRC-OWASP-AGENCY | [OWASP Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) | Risks caused by excessive LLM functionality, permissions, or autonomy |

## Citation Rules

1. Every framework claim must reference a `SRC-*` entry in this file.
2. Claims that this system ran or measured something require an `EVD-*` artifact;
   a framework source is not evidence of our implementation.
3. Stakeholder requirements use `REQ-*`; architecture decisions use `DEC-*`.
4. Re-check sources and licenses whenever an adapter version changes.
5. Do not interpret "open source" as "free hosted service, compute, storage, or
   operations."
