# B2 — Automatic Proposal

B2 consumes one B1-qualified opportunity, revalidates its assumptions, invokes
the shared A3 analysis and routes the resulting proposal according to risk and
owner policy.

## Package map

- `registry.py`: B2 handler lookup; B2.22 remains a mounted A3 subgraph.
- `shared.py`: artifact, staleness, risk, approval and proposal helpers.
- `nodes/`: direct native B2 implementations and explicit B2.22 subgraph marker.
- `__init__.py`: stable package exports.

## Flow

```mermaid
flowchart TD
    IN([QualifiedOpportunity in isolated case thread]) --> B210[B2.10 Verify opportunity]
    B210 --> B220[B2.20 Select analyzers]
    B220 --> B221[B2.21 Build bounded context]
    B221 --> B222[[B2.22 Shared A3 analysis]]
    B222 -->|A3.90 completed| B230[B2.30 Validate discovery assumptions]
    B222 -->|halt| STOP([Stop])
    B230 --> B231{B2.31 Staleness check}
    B231 -->|continue| B240[B2.40 Consolidate proposal]
    B231 -->|refresh or rejected| STOP
    B240 --> B241[B2.41 Produce explanation]
    B241 --> B250{B2.50 Risk and policy route}
    B250 -->|continue| B260[B2.60 Seal proposal]
    B250 -->|approval| B251[B2.51 Owner interrupt]
    B250 -->|rejected| STOP
    B251 --> B252{B2.52 Apply decision}
    B252 -->|continue| B260
    B252 -->|revision| B222
    B252 -->|rejected| STOP
    B260 --> OUT([C0 handoff])
```

## Invariants

- B2 accepts only a digest-valid, fresh `QualifiedOpportunity`.
- B2.22 reuses the exact A3 graph; it is not a duplicate implementation.
- Material discovery assumptions are revalidated before proposal publication.
- Approval binds the exact proposal digest, role, policy version and expiry.

