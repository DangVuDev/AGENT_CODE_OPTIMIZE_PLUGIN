# ADR-0001: Registry Port and NodePorts Expansion

Status: accepted
Scope: unblock A2.40/A2.50/A2.60-A2.64 and the B1 registration nodes
Code status: port + `NodePorts` wiring only; no new business handler is
authorized by this document

Update (2026-09-12): the blocked-node list described below as this ADR's
context has since been fully cleared — `A2_BLOCKED_NODES` in
`application/a2_handlers.py` is now an empty dict, and A2.50/A2.60-A2.95 all
have real handlers. The context, decision and consequences below are kept
as-written for the historical record of the port-design reasoning; they no
longer describe the current blocked-node state.

## Context

`A2_BLOCKED_NODES` (`application/a2_handlers.py`) documents twelve A2 nodes
that stay unregistered because they would otherwise have to fabricate a
capability the platform does not expose through a port:

| Node | Missing capability |
|---|---|
| A2.50 | authorize argv/write-roots/egress/secret leases/worker jobs |
| A2.60-A2.64 | run a real static/test/benchmark/telemetry/source-map collector or analyzer |
| A2.70-A2.95 | consume real evidence produced by A2.60-A2.64 |

Two different problems are tangled together here:

1. **Execution capability** — `PolicyPort`, `SecretsBroker` and `WorkerBroker`
   already exist as protocols (`ports/policy.py`, `ports/secrets.py`,
   `ports/workers.py`) and already have production adapters
   (`DeterministicPythonPolicy`, `LocalWorkerBroker`,
   `KubernetesWorkerBroker`), but `NodePorts` (`application/node_runtime.py`)
   never exposes them to a handler. A2.50 cannot be written today purely
   because of this wiring gap, independent of anything else.
2. **Capability lookup** — A2.40 currently resolves which collector answers
   an evidence requirement from `_COLLECTOR_CATALOG`, a hardcoded dict
   deliberately labeled as a stand-in (see the comment above it). The real
   shape already exists as `CollectorRegistration`/`AnalyzerRegistration`
   inside `RegistryRecord` (`contracts/registries.py`), and B1.20 needs the
   same lookup for repository/feature/owner registrations. There is no port
   that resolves a `RegistryRecord` at runtime — only the contract shape.

Both problems block real progress on A2.50+ and on B1 regardless of which
collector/analyzer/worker implementation eventually runs underneath.

## Decision

### 1. Add `RegistryPort`

`ports/registries.py` defines:

```python
class RegistryPort(Protocol):
    def resolve(
        self, *, tenant_id: str, registry_kind: RegistryKind, record_id: str, at: datetime
    ) -> RegistryRecord | None: ...

    def list_active(
        self, *, tenant_id: str, registry_kind: RegistryKind, at: datetime
    ) -> list[RegistryRecord]: ...
```

`resolve`/`list_active` are the only two operations every registration node
in the playbook actually needs (B1.20's "load tenant-approved registrations
at pinned versions", A2.40's "bind requirement to collector", A2.50's
"authorize" reading policy/collector registrations to check they're
enabled). The port is deliberately read-only: registries are written through
an operational/admin path (out of scope here), never by a business node.

An adapter is **not** part of this decision. The first adapter will most
likely be a `StaticRegistryAdapter` that loads one `RegistrySnapshot`
artifact per tenant and answers `resolve`/`list_active` in memory — matching
how `_COLLECTOR_CATALOG` behaves today, just moved behind the port and made
versioned/auditable via `RegistrySnapshot`. A live registry service (HTTP,
database-backed) can implement the same `Protocol` later without touching
any handler.

### 2. Expand `NodePorts`

`NodePorts` gains four new optional fields, all defaulting to `None`:

```python
@dataclass(frozen=True, slots=True)
class NodePorts:
    artifacts: ArtifactStore
    intents: IntentLedger
    telemetry: TelemetryPort | None = None
    policy: PolicyPort | None = None
    secrets: SecretsBroker | None = None
    workers: WorkerBroker | None = None
    registry: RegistryPort | None = None
```

They default to `None` for the same reason `telemetry` already does:
composing a runtime for a lane that doesn't need a given capability (every
existing A1/A2 test, `demo_a1_flow.py`) must not be forced to construct a
fake `PolicyPort`/`SecretsBroker`/`WorkerBroker`/`RegistryPort`. A handler
that requires one of these four checks for `None` explicitly and raises,
mirroring the existing `if ports is None: raise RuntimeError(...)` guard
already used by every production handler — never silently degrades.

Adding the fields does not enable any node. `A2_BLOCKED_NODES` stays
authoritative until a handler is actually written and registered; a blocked
entry is removed from that dict only in the same change that adds its
handler.

## Consequences

- A2.50 can now be implemented as soon as its handler logic is written —
  the port wiring blocker is gone, only the handler itself remains.
- A2.40 can be migrated from `_COLLECTOR_CATALOG` to `ports.registry` once a
  `RegistryPort` adapter exists, without changing `CollectorPlan`'s shape.
- B1.20 (registered source set) has a concrete port to implement against
  instead of inventing its own registry access pattern.
- A2.60-A2.64 remain blocked: `RegistryPort` tells a handler *which*
  collector/analyzer to use, it does not run one. Actually executing static
  analysis, tests, benchmarks or telemetry queries still needs a sandboxed
  execution path through `WorkerBroker` plus per-capability adapters
  (analyzer runner, test runner, telemetry query client) that this ADR does
  not create.
- No existing test, adapter, or handler changes behavior: every new field is
  optional and no registration set changes.

## Alternatives considered

- **Bake registry lookups into each handler via direct adapter import**
  (skip the port). Rejected — it would defeat `NodePorts`' purpose of making
  every capability a handler uses observable and swappable, and would make
  every A2/B1 handler untestable without real infrastructure.
- **One combined `RegistryPort` per `RegistryKind`** (e.g. separate
  `CollectorRegistryPort`, `PolicyRegistryPort`). Rejected for now — the
  read pattern (`resolve` by kind + id, `list_active` by kind) is identical
  across all nine `RegistryKind` values; a single port keyed by
  `registry_kind` avoids nine near-identical protocols for zero behavioral
  gain, and can still be split later if one kind's access pattern diverges.
