# 02. End-to-End Workflow

## Node Inventory

```text
A1  Manual requirement intake
A2  Real baseline and evidence
A3  Grounded findings and solutions
B1  Automatic bottleneck discovery and baseline
B2  Automatic grounded proposal
C0  Lane convergence gate
01  Re-rank and select a solution
02  Create the plan and task list
03  Implement one phase
04  Verify one phase
05  Perform controlled remeasurement
06  Decide KEEP / FIX_ONE_PART / REVERT
07  Produce an audited report
08  Perform progressive rollout
```

## Main Flow

```mermaid
flowchart TD
    START((Start)) --> ROUTE{Entry mode}
    ROUTE -->|Manual| A1[A1 Capture requirement]
    A1 --> A1G{Valid and approved?}
    A1G -->|No| A1I[[LangGraph interrupt: clarify or approve]]
    A1I --> A1
    A1G -->|Yes| A2[A2 Collect real baseline]
    A2 --> A2G{Comparable?}
    A2G -->|No| A2I[[Interrupt: repair or supply evidence]]
    A2I --> A2
    A2G -->|Yes| A3[A3 Findings and solutions]

    ROUTE -->|Automatic| B1[B1 Scan history and build baseline]
    B1 --> B1G{Valid opportunity?}
    B1G -->|No| WAIT[Wait for the next scan window]
    WAIT --> B1
    B1G -->|Yes, sealed case-start handoff| B2[B2 Create and route proposal in case thread]

    A3 --> C0[C0 Convergence gate]
    B2 --> C0
    C0 --> C0G{Grounded and eligible?}
    C0G -->|No| REVISE[Return to A3 or B2]
    REVISE --> A3
    C0G -->|Yes| S01[01 Re-rank and select]
    S01 --> S01G{Human approval required?}
    S01G -->|Yes| S01I[[Interrupt: approve, revise, or reject]]
    S01I --> S01
    S01G -->|No| S02[02 Plan and task list]

    S02 --> S02G{Critic and validator pass?}
    S02G -->|No, manual origin| A3
    S02G -->|No, automatic origin| B2
    S02G -->|Yes| S03[03 Implement one phase]
    S03 --> S04[04 Test and verify the phase]
    S04 --> S04G{Pass?}
    S04G -->|No| S03
    S04G -->|Yes| S05[05 Controlled remeasurement]
    S05 --> S05G{Comparable?}
    S05G -->|No| S05
    S05G -->|Yes| S06[06 Policy decision]

    S06 -->|KEEP| MORE{More phases?}
    MORE -->|Yes| S03
    MORE -->|No| S07[07 Audited report]
    S06 -->|FIX_ONE_PART| S03
    S06 -->|REVERT| RB[Execute rollback]
    RB --> S01
    S07 --> S08[08 Progressive rollout]
    S08 --> ROLL{Guardrails pass?}
    ROLL -->|No| RBR[Rollback rollout]
    RBR --> S07
    ROLL -->|Yes| CLOSED((Closed and observed))
    CLOSED -->|New bottleneck| ROUTE
```

## LangGraph Hierarchy

```mermaid
flowchart LR
    ROOT[OptimizationRootGraph]
    ROOT --> IA[LaneASubgraph]
    ROOT --> IB[LaneBSubgraph]
    ROOT --> SH[SharedOptimizationSubgraph]
    SH --> EX[PhaseExecutionSubgraph]
    SH --> RO[RolloutSubgraph]
    IA --> A1
    IA --> A2
    IA --> A3
    IB --> B1
    IB --> B2
    SH --> C0
    SH --> N01[01]
    SH --> N02[02]
    EX --> N03[03]
    EX --> N04[04]
    EX --> N05[05]
    EX --> N06[06]
    SH --> N07[07]
    RO --> N08[08]
```

Every transition must pass through a LangGraph conditional edge. An external
adapter returns a typed result to its node; it never chooses the next node or
mutates graph state directly.

For Lane B, the direct B1-to-B2 arrow is a logical product transition. One B1
scan can qualify multiple opportunities; B1.96 publishes an idempotent outbox
command and the same root graph creates one isolated B2 case/thread per sealed
opportunity under DEC-ARCH-003.

## Mandatory Interrupt Points

| Location | Pause condition | Resume payload |
|---|---|---|
| A1 | Ambiguous request, policy conflict, production approval | Decision, actor, clarification, request digest |
| A2 | Missing, stale, or incomparable evidence | Evidence references or revised request |
| B2 | Owner review of an automatically generated proposal | Approve, revise, or reject with rationale |
| 01 | Security, migration, code, architecture, or broad-radius risk | Selected solution, actor, rationale |
| 02 | Plan commands or permissions exceed policy | Approved phases or revision |
| 03 | Agent requests a tool or permission outside the allowlist | Approved tool-call digest |
| 06 | Policy requires a business-owner decision | KEEP, FIX_ONE_PART, or REVERT |
| 08 | Canary, gradual, or full promotion | Cohort, actor, observation window |

A LangGraph interrupt persists a checkpoint and resumes with the same
`thread_id`. Code before an interrupt may run again, so side effects must be
idempotent [SRC-LG-INTERRUPT].
