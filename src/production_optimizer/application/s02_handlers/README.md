# S02 — Plan and Task List

S02 turns S01's `SelectedSolution` into an executable, phased plan. It resolves
exact source targets, drafts ordered phases and tasks, and validates the draft
deterministically before anything downstream can implement it.

## Package map

- `registry.py`: S02 handler lookup.
- `shared.py`: model, draft-validation and revision-loop helpers.
- `nodes/`: one direct implementation per S02 node.
- `__init__.py`: stable package exports.

## Flow

```mermaid
flowchart TD
    IN([SelectedSolution]) --> S0210[S02.10 Revalidate scope]
    S0210 -->|rejected| STOP([Stop])
    S0210 -->|continue| S0220[S02.20 Discover test dependencies]
    S0220 --> S0230[S02.30 Draft phases and tasks]
    S0230 --> S0240[S02.40 Materialize phases]
    S0240 --> S0250[S02.50 Materialize tasks]
    S0250 --> S0260[S02.60 Check done-criteria coverage]
    S0260 --> S0270[S02.70 Check rollback commands]
    S0270 --> S0280[S02.80 Independent critic review]
    S0280 --> S0281{S02.81 Deterministic validation}
    S0281 -->|revision| S0230
    S0281 -->|rejected| STOP
    S0281 -->|continue| S0290{S02.90 Approve and seal}
    S0290 -->|approval| WAIT([Wait for owner decision])
    S0290 -->|rejected| STOP
    S0290 -->|continue| OUT([S03 handoff])
```

## Invariants

- Every phase changes exactly one logical treatment.
- Diagnostic (cheap, reversible) phases must sequence strictly before any
  implementation (expensive) phase -- `phase_ordering_by_risk` at S02.81.
- `ExecutionPlan`/`TaskList`/`PlanQualityReport` are sealed only by S02.81, and
  only once every deterministic dimension passes -- a broken draft never gets
  a fabricated digest.
- The revision loop (S02.30 <-> S02.81) is bounded and recorded in state.
- A `code`/`architecture` risk-ceiling strategy always halts for a real,
  resumable owner approval at S02.90.
