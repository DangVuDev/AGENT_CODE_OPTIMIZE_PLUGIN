# A3 — Grounded Problem and Solution Analysis

A3 turns the approved request and real baseline into evidence-cited findings and
an eligible solution portfolio. It proposes changes but does not modify source.

## Package map

- `registry.py`: A3 handler lookup.
- `shared.py`: model, artifact, citation, quality and revision helpers.
- `nodes/`: one direct implementation per A3 node.
- `__init__.py`: stable package exports.

## Flow

```mermaid
flowchart TD
    IN([OptimizationRequest and BaselineSnapshot]) --> A310[A3.10 Validate analysis inputs]
    A310 --> A311[A3.11 Build bounded context]
    A311 --> A320[A3.20 Extract candidate signals]
    A320 --> A321[A3.21 Prioritize signals]
    A321 --> FAN{Parallel grounded analysis}
    FAN --> A330[A3.30 Structural analysis]
    FAN --> A331[A3.31 Semantic analysis]
    FAN --> A332[A3.32 Domain analyzer evidence]
    FAN --> A333[A3.33 Runtime and telemetry analysis]
    A330 --> A340[A3.40 Draft findings]
    A331 --> A340
    A332 --> A340
    A333 --> A340
    A340 --> A341[A3.41 Resolve citations]
    A341 --> A350[A3.50 Judge findings]
    A350 --> A351{A3.51 Finding quality gate}
    A351 -->|rejected| STOP([Stop])
    A351 -->|continue| A360[A3.60 Generate strategies]
    A360 --> A361[A3.61 Bind strategy evidence]
    A361 --> A362[A3.62 Forecast criterion impact]
    A362 --> A363[A3.63 Analyze tradeoffs]
    A363 --> A364[A3.64 Assess implementation risk]
    A364 --> A370[A3.70 Rank solution portfolio]
    A370 --> A380[A3.80 Validate portfolio]
    A380 --> A381{A3.81 Quality decision}
    A381 -->|revision| A382[A3.82 Bounded revision]
    A382 --> A340
    A381 -->|rejected| STOP
    A381 -->|continue| A390[A3.90 Seal findings and solutions]
    A390 --> OUT([C0 handoff])
```

## Invariants

- Findings must cite evidence or source references from the sealed context.
- Model output is schema-validated and repaired only within bounded retries.
- A3 does not claim measured improvement and never edits the repository.
- Revision loops are bounded and recorded in state.

