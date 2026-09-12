# 01. Project Scaffold — As Built

Assessment date: 2026-09-12  
Scope: platform scaffold plus fail-closed orchestration frame

## Implemented Boundary

The repository now contains the application frame required before business
handler implementation:

- pinned Python project metadata, `uv.lock`, isolated `.venv`, and quality-tool
  configuration;
- one `langgraph.json` export for `OptimizationRootGraph`;
- a compiled root graph with manual, discovery and qualified-case entrypoints;
- compiled A1/A2/A3/B1/B2/C0 topology containing all 99 catalog task IDs;
- exact shared A2 and A3 subgraphs reused at B1.95 and B2.22;
- compact artifact, error, event, interrupt, command and root-state contracts;
- deterministic branch reducers and a handler runtime that validates declared
  routes and protects workflow identity;
- a pooled PostgreSQL LangGraph checkpointer provider with strict allowlisted
  serialization and explicit startup/shutdown ownership;
- the universal future `NodeSpec` operating contract;
- ports for checkpoints, cases, idempotency intents, artifacts, identity,
  policy, workers, secrets and telemetry;
- framework-neutral readiness aggregation;
- development Compose definitions for PostgreSQL, MinIO, OPA, OpenTelemetry
  Collector and Prometheus;
- initial control-plane tables for cases, node intents and the transactional
  outbox; and
- topology and routing tests for both lanes and C0.

All 99 business node wrappers now also have real, registered production
handlers (see `04-orchestration-frame-as-built.md` for the current business
handler and adapter inventory); a node without an explicit registration still
raises `NodeNotEnabledError` rather than emitting placeholder success — the
fail-closed default this scaffold established still holds, it is just no
longer the state of every node.

## Structure

```text
/
|-- pyproject.toml, .python-version, langgraph.json
|-- .env.example, .gitignore, .dockerignore
|-- src/production_optimizer/
|   |-- api/health.py
|   |-- application/node_contract.py, application/node_runtime.py
|   |-- contracts/
|   |-- orchestration/root_graph.py, orchestration/lanes.py
|   |-- orchestration/catalog.py, orchestration/subgraphs/
|   |-- ports/
|   |-- adapters/development/, adapters/production/
|   `-- observability/
|-- migrations/001_control_plane.sql
|-- deploy/development/
|-- deploy/production/README.md
|-- policies/bootstrap.rego
`-- tests/unit/, tests/contract/
```

## Intentional Gaps Before FRAME-0

1. The canonical organization Git remote and governance remain unset because
   the workspace was supplied without a Git repository or remote URL.
2. Development images use explicit tags; production requires reviewed immutable
   digests and separate manifests.
3. The PostgreSQL provider and strict serialization are implemented, but live
   database migration, schema separation, HA and restart/resume tests are not
   yet wired.
4. Infrastructure ports now have concrete production adapters and unit/contract
   test coverage (see `04-orchestration-frame-as-built.md`); live acceptance
   evidence against real managed infrastructure (HA Postgres, a real S3/MinIO
   fleet, a real Kubernetes cluster) is still a separate deployment gate.
5. Interrupt payloads are modeled, and a real, reusable human-in-the-loop
   resume/approval path (`application/resume.py`) is implemented and used by
   A1.90, A2.31's LLM-suggested-command approval, and B2's owner-review
   routing; durable checkpoint behavior beyond the tested restart scenarios
   still requires further FRAME-0 integration.
6. B1's multi-candidate `Send` fan-out is still not implemented (B1 currently
   processes detected signals without that parallel fan-out primitive); every
   other business handler is implemented and registered.

The lockfile environment, tests and static checks complete the technical part of
`BOOT-1`; repository governance remains blocked on the organization remote. The
next permissible increment is validating the running `BOOT-2` local stack,
followed by production port adapters and `FRAME-0` platform smoke behavior.
The static `FRAME-1` topology is present, but enabling any handler remains
blocked on those gates.

## Verification Evidence

| Check | Result |
|---|---|
| Locked dependency resolution | 55 packages resolved; Psycopg binary/pool pinned |
| Unit/contract tests | 39 passed |
| Behavioral test coverage | Above configured 90% minimum |
| Ruff | Passed |
| Pyright strict | 0 errors, 0 warnings |
| Docker Compose configuration | Validated successfully |
| Catalog business task IDs in expanded graph | 99 |
| Enabled business handlers | 99/99, real production handlers registered |
