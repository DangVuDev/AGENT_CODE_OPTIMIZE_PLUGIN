# A1 — Requirement Intake

A1 converts a manual optimization request into one approved, immutable
`OptimizationRequest`. It resolves ambiguity and validates scope before A2 is
allowed to execute anything.

## Package map

- `registry.py`: A1 node specifications, allowed routes and runtime registration.
- `shared.py`: artifact, source-discovery, model-extraction and validation helpers.
- `nodes/`: one directly executable implementation per A1 business node.
- `__init__.py`: stable package exports.

## Flow

```mermaid
flowchart TD
    IN([ManualCasePayload]) --> A110[A1.10 Authenticate and initialize intake]
    A110 --> A120[A1.20 Normalize structured or raw request]
    A120 --> A130[A1.30 Resolve source and Git identity]
    A130 --> A140[A1.40 Discover project structure]
    A140 --> A150[A1.50 Resolve feature scope]
    A150 --> A160[A1.60 Canonicalize objective]
    A160 --> A161[A1.61 Define measurable criteria]
    A161 --> A162[A1.62 Define guardrails]
    A162 --> A163[A1.63 Freeze execution budget]
    A163 --> A170[A1.70 Resolve workload]
    A170 --> A171[A1.71 Bind evidence requirements]
    A171 --> A180{A1.80 Quality gate}
    A180 -->|continue| A190{A1.90 Policy and approval}
    A180 -->|clarification| WAIT([Wait for clarification])
    A180 -->|rejected| STOP([Stop])
    A190 -->|continue| A195[A1.95 Seal OptimizationRequest]
    A190 -->|approval| APPROVAL([Wait for approval])
    A190 -->|rejected| STOP
    A195 --> OUT([A2 handoff])
```

## Invariants

- Source paths must remain inside the approved root.
- Unknown or conflicting request fields are never silently invented.
- Every criterion and guardrail must have an evidence requirement.
- A2 starts only after A1.80 passes and A1.90 authorizes the exact digest.

