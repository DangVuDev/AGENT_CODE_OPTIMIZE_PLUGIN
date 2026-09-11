from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from .artifacts import ArtifactRef
from .events import EventRef
from .interrupts import InterruptEnvelope


def merge_artifact_refs(left: list[ArtifactRef], right: list[ArtifactRef]) -> list[ArtifactRef]:
    """Deterministically merge parallel references and reject digest conflicts."""

    merged: dict[tuple[str, str], ArtifactRef] = {}
    for ref in [*left, *right]:
        key = (ref.artifact_type, ref.artifact_id)
        existing = merged.get(key)
        if existing is not None and existing.content_digest != ref.content_digest:
            raise ValueError(f"artifact reference conflict for {key[0]}:{key[1]}")
        merged[key] = ref
    return [merged[key] for key in sorted(merged)]


def merge_node_ids(left: list[str], right: list[str]) -> list[str]:
    """Merge branch completion markers in stable catalogue order."""

    return sorted(set(left) | set(right), key=lambda value: (value.split(".")[0], float(value[1:])))


def merge_node_routes(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Merge per-node routes and reject nondeterministic replay outcomes."""

    merged = dict(left)
    for node_id, route in right.items():
        existing = merged.get(node_id)
        if existing is not None and existing != route:
            raise ValueError(f"node route conflict for {node_id}: {existing!r} != {route!r}")
        merged[node_id] = route
    return dict(sorted(merged.items()))


def latest_node(left: str, right: str) -> str:
    """Select a stable progress marker when parallel branches complete."""

    if "." not in left:
        return right
    if "." not in right:
        return left
    left_stage, left_number = left.split(".", maxsplit=1)
    right_stage, right_number = right.split(".", maxsplit=1)
    return max((left_stage, int(left_number), left), (right_stage, int(right_number), right))[2]


class OptimizationState(TypedDict, total=False):
    """Compact root state.

    Raw source, evidence, command output, model transcripts, and secrets are
    deliberately absent. Business-stage fields are references reserved for
    later Lane 1 work.
    """

    case_id: str
    thread_id: str
    tenant_id: str
    lane: Literal["manual", "automatic"]
    entrypoint: Literal["manual", "discovery", "qualified"]
    baseline_mode: Literal["active_collection", "historical_recovery"]
    status: str
    current_node: Annotated[str, latest_node]
    request_ref: ArtifactRef
    baseline_ref: ArtifactRef
    solution_portfolio_ref: ArtifactRef
    convergence_ref: ArtifactRef
    pending_interrupt: InterruptEnvelope | None
    completed_nodes: Annotated[list[str], merge_node_ids]
    node_routes: Annotated[dict[str, str], merge_node_routes]
    artifact_refs: Annotated[list[ArtifactRef], merge_artifact_refs]
    error_refs: Annotated[list[ArtifactRef], merge_artifact_refs]
    event_refs: Annotated[list[EventRef], operator.add]
