# Production Code Optimization Platform

This documentation set defines the target design for a production-grade,
evidence-grounded automatic and semi-automatic code optimization platform. It is
an implementation specification, not a demo description.

## Contents

Documents marked **canonical** define enforceable contracts. Summary documents
must link to them and must not redefine schemas or business rules.

| File | Content |
|---|---|
| [00-requirements-and-decisions.md](00-requirements-and-decisions.md) | **Canonical:** stakeholder requirements, architecture decisions, scope profiles, and traceability |
| [01-product-vision.md](01-product-vision.md) | Problem, scope, principles, and success criteria |
| [02-end-to-end-workflow.md](02-end-to-end-workflow.md) | Complete Lane A, Lane B, and shared steps 01-08 |
| [03-business-nodes.md](03-business-nodes.md) | Business purpose, inputs, processing, outputs, gates, and failures for every node |
| [04-langgraph-architecture.md](04-langgraph-architecture.md) | LangGraph control-plane architecture for the complete workflow |
| [05-framework-matrix.md](05-framework-matrix.md) | Framework fit, limitations, and complementary components |
| [06-data-contracts.md](06-data-contracts.md) | **Canonical:** state, artifact vocabulary, evidence, provenance, and schema conventions |
| [07-implementation-roadmap.md](07-implementation-roadmap.md) | Milestones, deliverables, and release gates |
| [08-production-readiness.md](08-production-readiness.md) | Security, reliability, observability, SLOs, and operations |
| [09-agent-integration.md](09-agent-integration.md) | Packaging as a tool, skill, MCP server, or sub-agent |
| [10-current-gap-analysis.md](10-current-gap-analysis.md) | Evidence-based current implementation status and gaps |
| [11-sources.md](11-sources.md) | Official sources for framework claims and design decisions |
| [12-production-operating-contracts.md](12-production-operating-contracts.md) | **Canonical:** ownership, SLO, idempotency, error, retry, and runbook contracts per stage |
| [../implementation/00-bootstrap-and-build-order.md](../implementation/00-bootstrap-and-build-order.md) | **Pre-implementation authority:** what is cloned/installed/pulled, repository bootstrap, platform frame, infrastructure ports, and ordered gates before business nodes |
| [../implementation/01-project-scaffold.md](../implementation/01-project-scaffold.md) | **As-built scaffold:** files, implemented platform boundary, intentional gaps, and next permitted increment |
| [../implementation/02-two-lane-product-architecture.md](../implementation/02-two-lane-product-architecture.md) | **Two-lane architecture authority:** root execution, scan/case isolation, complete Lane A/Lane B/C0 topology, shared A2/A3 modes, framework ownership and product boundary |
| [../implementation/03-two-lane-delivery-roadmap.md](../implementation/03-two-lane-delivery-roadmap.md) | **Two-lane delivery authority:** node-by-node implementation waves, framework/adapters, test matrix, promotion and product definition of done |
| [../implementation/04-orchestration-frame-as-built.md](../implementation/04-orchestration-frame-as-built.md) | **As-built orchestration evidence:** implemented root/subgraph topology, runtime safety boundary, verification and deliberately disabled capabilities |
| [../implementation/lane-1-langgraph-architecture.md](../implementation/lane-1-langgraph-architecture.md) | **Lane 1 implementation authority:** exhaustive A1.10-A3.90 graph topology, state, reducers, node runtime, interrupts, and framework ownership |
| [../implementation/lane-1-a1-a3-plan.md](../implementation/lane-1-a1-a3-plan.md) | Lane 1 delivery sequence, package boundaries, verification, and completion claims |
| [lane-a-local-codebase/README.md](lane-a-local-codebase/README.md) | Senior BA specification for A1, A2, and A3 when processing local codebases |
| [lane-b-local-codebase/README.md](lane-b-local-codebase/README.md) | Senior BA specification for automatic discovery and proposal against local codebases |
| [shared-workflow/README.md](shared-workflow/README.md) | Senior BA specification for convergence and shared steps 01-08 |

## Document Authority

| Concern | Canonical source | Secondary summaries |
|---|---|---|
| Product requirements and scope | `00-requirements-and-decisions.md` | `01-product-vision.md` |
| Workflow topology | `02-end-to-end-workflow.md` and `04-langgraph-architecture.md` | Folder READMEs |
| Business rules | Lane A, Lane B, and shared-workflow task documents | `03-business-nodes.md` |
| Artifact names and versions | `06-data-contracts.md` | All task documents |
| Framework adoption | `05-framework-matrix.md` | Per-task framework-fit columns |
| As-built status | `10-current-gap-analysis.md` | Roadmap and target architecture |

## Architectural Position

```text
LangGraph = workflow controller and source of workflow truth
External frameworks = adapters that provide specialized capabilities
Policy code = authority for gates and KEEP/FIX_ONE_PART/REVERT decisions
LLM = proposal, analysis, and criticism; never evidence self-approval
```

Every capability from A1 through 08 must be represented by a LangGraph node or
subgraph. No external framework may skip a gate, mutate workflow state, replace
the baseline, or independently authorize a rollout.
