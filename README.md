# Production Code Optimization Platform

This repository contains the production blueprint and the fail-closed
orchestration frame for an evidence-grounded code optimization control plane.

Current implementation boundary:

- project packaging and quality configuration;
- a loadable LangGraph root with manual, discovery, and qualified-case routes;
- compiled A1/A2/A3/B1/B2/C0 subgraphs covering all 99 documented task IDs;
- shared A2/A3 graph implementations, including B1.95 and B2.22 reuse;
- compact workflow-state, command, reducer, and operating contracts;
- a validated node runtime that rejects missing handlers, undeclared routes,
  unknown state fields, and identity mutation;
- a production-scoped A1 manual-intake handler slice with typed intake, draft,
  source identity, project profile, feature scope, quality and request-freeze
  artifacts;
- a deterministic pilot runtime that registers all 99 nodes and runs manual,
  discovery, and qualified-case flows with strict contract artifact references;
- a pooled PostgreSQL LangGraph checkpointer provider with an explicit strict
  serialization allowlist and no pickle fallback;
- PostgreSQL control-plane adapters, S3/MinIO artifacts, JWT identity, deterministic
  policy, OpenTelemetry, local/Kubernetes worker brokers and resume authorization;
- strict A1-C0 Pydantic contracts, generated JSON Schemas, versioned registries,
  canonical digests and a fail-closed 99-node manifest;
- development infrastructure composition and versioned database migrations; and
- architecture, topology, routing, platform, contract and restart tests.

The default production runtime remains fail-closed: invoking a lane without an
explicit Definition-of-Ready registration still stops at the first unavailable
node. A1 manual intake now has production-oriented handlers, while A2/A3/B1/B2/C0
production handlers remain separate delivery waves. `build_pilot_runtime()`
provides a deterministic local flow for contract and orchestration validation
only; it does not execute collectors, analyzers, models, workers or source
changes. The required outside-in construction order is documented in
[`docs/implementation/00-bootstrap-and-build-order.md`](docs/implementation/00-bootstrap-and-build-order.md).
The full Lane A/Lane B/C0 target and delivery sequence are documented in
[`02-two-lane-product-architecture.md`](docs/implementation/02-two-lane-product-architecture.md)
and
[`03-two-lane-delivery-roadmap.md`](docs/implementation/03-two-lane-delivery-roadmap.md).
The exact implemented boundary is recorded in
[`04-orchestration-frame-as-built.md`](docs/implementation/04-orchestration-frame-as-built.md).
The node-by-node business and technical delivery contracts are defined in the
[`Lane 1 playbook`](docs/implementation/05-lane-1-detailed-implementation-playbook.md)
and
[`Lane 2 playbook`](docs/implementation/06-lane-2-detailed-implementation-playbook.md).

## Local prerequisites

- Python 3.13.1
- Docker Engine with Compose v2
- Git
- `uv` 0.12.10 (the lockfile records the project environment)

Copy `.env.example` to an ignored `.env` before starting local infrastructure.
Values in `.env.example` are development defaults and are not production
credentials or production image approvals.

## Planned bootstrap commands

```powershell
python -m uv sync --all-groups --frozen
docker compose --env-file .env -f deploy/development/compose.yaml config
python -m uv run pytest
python -m uv run ruff check src tests
python -m uv run pyright
```

Production deployment remains blocked until organization-owned repository
governance and the `BOOT-*`/`FRAME-*` gates are completed.
