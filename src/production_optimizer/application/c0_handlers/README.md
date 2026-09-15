# C0 — Cross-Lane Convergence

C0 is the lane-neutral gate that proves Lane A and Lane B produced equivalent,
fresh and internally consistent optimization packages before shared execution
and selection stages begin.

## Package map

- `registry.py`: C0 handler lookup.
- `shared.py`: schema, digest, freshness, artifact and Git helpers.
- `nodes/`: one direct implementation per convergence node.
- `__init__.py`: stable package exports.

## Flow

```mermaid
flowchart TD
    A([Lane A: A1 + A2 + A3]) --> C010[C0.10 Validate origin]
    B([Lane B: B1 + B2]) --> C010
    C010 --> C020[C0.20 Validate schemas]
    C020 --> C030[C0.30 Validate digest chain]
    C030 --> C040[C0.40 Verify semantic equivalence]
    C040 --> C050[C0.50 Check freshness]
    C050 --> C060{C0.60 Convergence gate}
    C060 -->|continue| C070[C0.70 Seal ConvergedCase]
    C060 -->|rejected| STOP([Stop before shared workflow])
    C070 --> OUT([Shared workflow handoff])
```

## Invariants

- Origin is retained for audit but cannot weaken quality requirements.
- Artifact schema versions and canonical digests must validate.
- Lane B must meet the same request, baseline and solution quality bar as Lane A.
- Stale source, evidence, approval or ownership blocks convergence.
- C0 reports reasons deterministically and never repairs producer artifacts.

