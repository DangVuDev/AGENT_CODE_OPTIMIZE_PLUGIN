# pyright: reportPrivateUsage=false
"""Implementation of business node B1.40."""

from __future__ import annotations

from datetime import timedelta

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import (
    HistoricalEvidenceBranch,
    HistoricalEvidenceItem,
    RunGroup,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _QUERY_BRANCH_IDS,
    _read_model,
    _require_branch_ref,
)


def handle_b1_40_preserve_raw_records_normalize_registered_units_group(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Group whatever real evidence the query branches actually returned.

    With every branch honestly empty today, this correctly produces zero
    `RunGroup`s -- the moment a real query adapter returns evidence, this
    groups it by (feature, workload, environment) exactly as documented, no
    code change needed here.
    """

    branches = [
        _read_model(ports, state, _require_branch_ref(state, node_id), HistoricalEvidenceBranch)
        for node_id in _QUERY_BRANCH_IDS
    ]
    all_evidence = [item for branch in branches for item in branch.evidence]

    groups: dict[tuple[str, str, str], list[HistoricalEvidenceItem]] = {}
    for item in all_evidence:
        key = (item.feature_id, item.workload_id, item.environment_id)
        groups.setdefault(key, []).append(item)

    run_groups = [
        RunGroup(
            group_id=f"run-group-{index}",
            feature_id=feature_id,
            workload_id=workload_id,
            environment_id=environment_id,
            sample_ids=[item.evidence_id for item in items],
            window_start=min(item.observed_at for item in items),
            window_end=max(item.observed_at for item in items) + timedelta(seconds=1),
            trust_level="T2",
        )
        for index, ((feature_id, workload_id, environment_id), items) in enumerate(
            groups.items(), start=1
        )
    ]
    return NodeExecution(updates={"b1_run_groups": run_groups})


__all__ = ["handle_b1_40_preserve_raw_records_normalize_registered_units_group"]
