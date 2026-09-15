from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

A1_NODE_IDS = (
    "A1.10",
    "A1.20",
    "A1.30",
    "A1.40",
    "A1.50",
    "A1.60",
    "A1.61",
    "A1.62",
    "A1.63",
    "A1.70",
    "A1.71",
    "A1.80",
    "A1.90",
    "A1.95",
)
A2_NODE_IDS = (
    "A2.10",
    "A2.20",
    "A2.30",
    "A2.31",
    "A2.40",
    "A2.41",
    "A2.50",
    "A2.60",
    "A2.61",
    "A2.62",
    "A2.63",
    "A2.64",
    "A2.70",
    "A2.71",
    "A2.80",
    "A2.90",
    "A2.91",
    "A2.95",
)
A3_NODE_IDS = (
    "A3.10",
    "A3.11",
    "A3.20",
    "A3.21",
    "A3.30",
    "A3.31",
    "A3.32",
    "A3.33",
    "A3.40",
    "A3.41",
    "A3.50",
    "A3.51",
    "A3.60",
    "A3.61",
    "A3.62",
    "A3.63",
    "A3.64",
    "A3.70",
    "A3.80",
    "A3.81",
    "A3.82",
    "A3.90",
)
B1_NODE_IDS = (
    "B1.10",
    "B1.20",
    "B1.21",
    "B1.30",
    "B1.31",
    "B1.32",
    "B1.33",
    "B1.34",
    "B1.35",
    "B1.40",
    "B1.41",
    "B1.50",
    "B1.51",
    "B1.52",
    "B1.53",
    "B1.60",
    "B1.61",
    "B1.62",
    "B1.70",
    "B1.71",
    "B1.80",
    "B1.81",
    "B1.90",
    "B1.91",
    "B1.95",
    "B1.96",
)
B2_NODE_IDS = (
    "B2.10",
    "B2.20",
    "B2.21",
    "B2.22",
    "B2.30",
    "B2.31",
    "B2.40",
    "B2.41",
    "B2.50",
    "B2.51",
    "B2.52",
    "B2.60",
)
C0_NODE_IDS = ("C0.10", "C0.20", "C0.30", "C0.40", "C0.50", "C0.60", "C0.70")

ALL_BUSINESS_NODE_IDS = frozenset(
    (*A1_NODE_IDS, *A2_NODE_IDS, *A3_NODE_IDS, *B1_NODE_IDS, *B2_NODE_IDS, *C0_NODE_IDS)
)

# Shared workflow steps 01-08 (docs/project-blueprint/shared-workflow/) consume
# a `ConvergedCase` -- the C0.70 handoff -- and are deliberately kept out of
# `ALL_BUSINESS_NODE_IDS`/the hardcoded `99` count above: that "99-node
# business workflow" figure is already the documented, synced boundary
# (A1-C0) across CLAUDE.md and docs/, and these steps are a separate,
# later-scoped continuation, not an expansion of it. S08 (Rollout) has no
# node IDs here yet -- deferred, contract-only, per project decision (no
# CONNECTED_PRODUCTION deployment target exists to route it against).
S01_NODE_IDS = (
    "S01.10",
    "S01.20",
    "S01.30",
    "S01.40",
    "S01.50",
    "S01.60",
    "S01.70",
    "S01.80",
    "S01.90",
)
S02_NODE_IDS = (
    "S02.10",
    "S02.20",
    "S02.30",
    "S02.40",
    "S02.50",
    "S02.60",
    "S02.70",
    "S02.80",
    "S02.81",
    "S02.90",
)
S03_NODE_IDS = (
    "S03.10",
    "S03.20",
    "S03.30",
    "S03.40",
    "S03.50",
    "S03.60",
    "S03.70",
    "S03.80",
    "S03.90",
)
S04_NODE_IDS = (
    "S04.10",
    "S04.20",
    "S04.30",
    "S04.40",
    "S04.50",
    "S04.60",
    "S04.70",
    "S04.80",
    "S04.90",
)
S05_NODE_IDS = (
    "S05.10",
    "S05.20",
    "S05.30",
    "S05.40",
    "S05.50",
    "S05.60",
    "S05.70",
    "S05.80",
    "S05.90",
)
S06_NODE_IDS = (
    "S06.10",
    "S06.20",
    "S06.30",
    "S06.40",
    "S06.50",
    "S06.60",
    "S06.70",
    "S06.80",
    "S06.90",
)
S07_NODE_IDS = (
    "S07.10",
    "S07.20",
    "S07.30",
    "S07.40",
    "S07.50",
    "S07.60",
    "S07.70",
    "S07.80",
    "S07.90",
)

SHARED_WORKFLOW_NODE_IDS = frozenset(
    (
        *S01_NODE_IDS,
        *S02_NODE_IDS,
        *S03_NODE_IDS,
        *S04_NODE_IDS,
        *S05_NODE_IDS,
        *S06_NODE_IDS,
        *S07_NODE_IDS,
    )
)


@dataclass(frozen=True, slots=True)
class FanOut:
    source: str
    branches: tuple[str, ...]
    join: str


A2_FAN_OUT = FanOut("A2.50", ("A2.60", "A2.61", "A2.62", "A2.63", "A2.64"), "A2.70")
A3_FAN_OUT = FanOut("A3.21", ("A3.30", "A3.31", "A3.32", "A3.33"), "A3.40")
B1_QUERY_FAN_OUT = FanOut("B1.31", ("B1.32", "B1.33", "B1.34", "B1.35"), "B1.40")
B1_DETECTOR_FAN_OUT = FanOut("B1.41", ("B1.50", "B1.51", "B1.52", "B1.53"), "B1.60")


def assert_catalog_integrity(
    groups: Iterable[tuple[str, ...]], *, expected_count: int = 99
) -> None:
    flattened = [node_id for group in groups for node_id in group]
    if len(flattened) != len(set(flattened)):
        raise ValueError("business node catalog contains duplicate task IDs")
    if len(flattened) != expected_count:
        raise ValueError(
            f"business node catalog must contain {expected_count} task IDs, found {len(flattened)}"
        )


assert_catalog_integrity(
    (A1_NODE_IDS, A2_NODE_IDS, A3_NODE_IDS, B1_NODE_IDS, B2_NODE_IDS, C0_NODE_IDS)
)
assert_catalog_integrity(
    (
        S01_NODE_IDS,
        S02_NODE_IDS,
        S03_NODE_IDS,
        S04_NODE_IDS,
        S05_NODE_IDS,
        S06_NODE_IDS,
        S07_NODE_IDS,
    ),
    expected_count=len(SHARED_WORKFLOW_NODE_IDS),
)
