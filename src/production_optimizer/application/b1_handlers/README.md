# B1 — Automatic Discovery and Qualification

B1 scans approved historical production evidence, detects optimization
opportunities, reconstructs an A1-equivalent request and recovers the baseline
through the shared A2 subgraph.

## Package map

- `registry.py`: B1 handler lookup; B1.95 remains a mounted A2 subgraph.
- `shared.py`: query, detection, binding, artifact and qualification helpers.
- `nodes/`: direct implementations for native B1 nodes and explicit subgraph marker for B1.95.
- `__init__.py`: stable package exports.

## Flow

```mermaid
flowchart TD
    IN([Scheduled or event-driven discovery trigger]) --> B110[B1.10 Initialize scan]
    B110 --> B120[B1.20 Load registered sources]
    B120 --> B121[B1.21 Fingerprint deployed source]
    B121 --> B130[B1.30 Inventory historical evidence]
    B130 --> B131[B1.31 Authorize bounded reads]
    B131 --> FANQ{Parallel historical queries}
    FANQ --> B132[B1.32 Metrics]
    FANQ --> B133[B1.33 Logs]
    FANQ --> B134[B1.34 Traces and profiles]
    FANQ --> B135[B1.35 LLM evidence]
    B132 --> B140[B1.40 Normalize and group runs]
    B133 --> B140
    B134 --> B140
    B135 --> B140
    B140 --> B141[B1.41 Evidence eligibility]
    B141 --> FAND{Parallel detectors}
    FAND --> B150[B1.50 Threshold breach]
    FAND --> B151[B1.51 Regression]
    FAND --> B152[B1.52 Hotspot and recurrent failure]
    FAND --> B153[B1.53 Quality and cost drift]
    B150 --> B160[B1.60 Bind feature]
    B151 --> B160
    B152 --> B160
    B153 --> B160
    B160 --> B161[B1.61 Bind source revision]
    B161 --> B162[B1.62 Resolve ownership]
    B162 --> B170[B1.70 Score opportunity]
    B170 --> B171{B1.71 Qualification gate}
    B171 -->|continue| B180{B1.80 Deduplicate}
    B171 -->|quarantine or rejected| STOP([Close candidate])
    B180 -->|continue| B181{B1.81 Cooldown gate}
    B180 -->|merged or suppressed| STOP
    B181 -->|continue| B190[B1.90 Build automatic OptimizationRequest]
    B181 -->|closed or suppressed| STOP
    B190 --> B191{B1.91 Automatic intake policy}
    B191 -->|continue| B195[[B1.95 Shared A2 historical recovery]]
    B191 -->|approval or rejected| STOP
    B195 -->|A2.95 completed| B196[B1.96 Seal QualifiedOpportunity]
    B195 -->|halt| STOP
    B196 --> OUT([Case-start handoff])
```

## Invariants

- B1 never launches a special discovery benchmark.
- Queries are tenant-scoped, read-only, time-bounded and policy-authorized.
- A candidate needs feature, source and owner bindings before qualification.
- B1.95 uses the exact A2 graph in `historical_recovery` mode.
- One scan may create zero or many isolated qualified opportunities.

