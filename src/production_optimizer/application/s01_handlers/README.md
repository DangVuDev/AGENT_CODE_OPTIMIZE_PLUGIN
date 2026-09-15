# S01 — Rank and Select

S01 is the first shared-workflow step after C0. It turns a `ConvergedCase`'s
eligible `SolutionPortfolio` into one selected strategy: re-ranked against the
criteria A1 froze, or handed to a human to decide when risk, sensitivity or
confidence say it should not auto-select.

## Package map

- `registry.py`: S01 handler lookup.
- `shared.py`: scoring, ranking, sensitivity and pass-scoping helpers.
- `nodes/`: one direct implementation per S01 node.
- `__init__.py`: stable package exports.

## Flow

```mermaid
flowchart TD
    IN([ConvergedCase / SolutionPortfolio]) --> S0110[S01.10 Freeze decision context]
    S0110 --> S0120[S01.20 Exclude ineligible strategies]
    S0120 -->|rejected| STOP([Stop])
    S0120 -->|continue| S0130[S01.30 Normalize scoring factors]
    S0130 --> S0140[S01.40 Weight and score]
    S0140 --> S0150[S01.50 Rank with risk-tier tiebreak]
    S0150 --> S0160[S01.60 Sensitivity sweep]
    S0160 --> S0170[S01.70 Decide if approval is required]
    S0170 --> S0180{S01.80 Seal ranking, auto-select or halt}
    S0180 -->|approval| WAIT([Wait for owner decision])
    S0180 -->|rejected| STOP
    S0180 -->|continue| S0190[S01.90 Seal SelectedSolution]
    S0190 --> OUT([S02 handoff])
```

## Invariants

- Ineligible, non-reversible, unresolved-scope or previously-REVERTed
  strategies never reach ranking (`BR-01-001`).
- Ranking prefers real score; a lower risk tier only breaks a genuine tie
  (`BR-01-003`), never overrides one.
- A `code`/`architecture` risk ceiling, a sensitivity-flagged ranking, or a
  low-confidence winner always halts for a real, resumable owner decision
  instead of auto-selecting (`BR-01-004`).
- A REVERT (S06.80) re-enters S01 with one more excluded strategy; every
  sealed artifact from that pass is pass-scoped so it never collides with
  the prior pass's (`BR-01-005`).
