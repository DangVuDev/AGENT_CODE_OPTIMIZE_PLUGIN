# A2 — Evidence Collection and Baseline

A2 materializes the approved source, executes only authorized repository-owned
workloads and produces a comparable, provenance-bound `BaselineSnapshot`.

## Package map

- `registry.py`: all 18 registrations, route policy, side-effect classes and runtime factory.
- `shared.py`: repository discovery, worker, evidence, artifact and normalization helpers.
- `nodes/`: direct implementation of every A2 node.
- `__init__.py`: stable public exports.

## Flow

```mermaid
flowchart TD
    IN([Approved OptimizationRequest]) --> A210{A2.10 Verify request and source}
    A210 -->|continue| A220[A2.20 Capture immutable source snapshot]
    A210 -->|rejected| STOP([Stop])
    A220 --> A230[A2.30 Parse repository manifest]
    A230 --> A231{A2.31 Resolve executable commands}
    A231 -->|approval| WAIT([Approve suggested command])
    A231 -->|continue| A240{A2.40 Build collector plan}
    A240 -->|missing| STOP
    A240 -->|continue| A241[A2.41 Freeze environment]
    A241 --> A250{A2.50 Authorize worker jobs}
    A250 -->|rejected| STOP
    A250 -->|continue| FOUT{Parallel evidence collection}
    FOUT --> A260[A2.60 Static and quality evidence]
    FOUT --> A261[A2.61 Correctness evidence]
    FOUT --> A262[A2.62 Performance evidence]
    FOUT --> A263[A2.63 Telemetry evidence]
    FOUT --> A264[A2.64 Source map evidence]
    A260 --> A270[A2.70 Fan-in raw evidence]
    A261 --> A270
    A262 --> A270
    A263 --> A270
    A264 --> A270
    A270 --> A271[A2.71 Normalize evidence]
    A271 --> A280[A2.80 Bind provenance]
    A280 --> A290{A2.90 Evidence quality gate}
    A290 -->|continue| A291{A2.91 Comparability gate}
    A290 -->|missing or rejected| STOP
    A291 -->|continue| A295[A2.95 Seal baseline]
    A291 -->|missing or incomparable| STOP
    A295 --> OUT([BaselineSnapshot and A3 handoff])
```

## Invariants

- A2 executes frozen `argv`; it does not generate arbitrary shell commands.
- Worker execution requires policy authorization and configured `NodePorts`.
- Raw samples are preserved before normalization or aggregation.
- Missing telemetry or benchmark capability is reported as unavailable, never fabricated.
- A2.95 fails closed unless quality and comparability both pass.

