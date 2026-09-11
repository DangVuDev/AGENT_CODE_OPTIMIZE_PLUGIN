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


@dataclass(frozen=True, slots=True)
class FanOut:
    source: str
    branches: tuple[str, ...]
    join: str


A2_FAN_OUT = FanOut("A2.50", ("A2.60", "A2.61", "A2.62", "A2.63", "A2.64"), "A2.70")
A3_FAN_OUT = FanOut("A3.21", ("A3.30", "A3.31", "A3.32", "A3.33"), "A3.40")
B1_QUERY_FAN_OUT = FanOut("B1.31", ("B1.32", "B1.33", "B1.34", "B1.35"), "B1.40")
B1_DETECTOR_FAN_OUT = FanOut("B1.41", ("B1.50", "B1.51", "B1.52", "B1.53"), "B1.60")


def assert_catalog_integrity(groups: Iterable[tuple[str, ...]]) -> None:
    flattened = [node_id for group in groups for node_id in group]
    if len(flattened) != len(set(flattened)):
        raise ValueError("business node catalog contains duplicate task IDs")
    if len(flattened) != 99:
        raise ValueError(f"business node catalog must contain 99 task IDs, found {len(flattened)}")


assert_catalog_integrity(
    (A1_NODE_IDS, A2_NODE_IDS, A3_NODE_IDS, B1_NODE_IDS, B2_NODE_IDS, C0_NODE_IDS)
)
