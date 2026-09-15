# pyright: reportPrivateUsage=false
"""Implementation of business node A2.70."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import BranchEvidenceRefs, RawEvidenceFanIn
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _FAN_IN_BRANCH_IDS,
    _base_envelope,
    _put_envelope,
    _read_model,
    _require_branch_ref,
    _seal,
)


def handle_a2_70_fan_stable_branch_identity_write_raw_bytes(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    branch_refs = [_require_branch_ref(state, node_id) for node_id in _FAN_IN_BRANCH_IDS]
    branches = [_read_model(ports, state, ref, BranchEvidenceRefs) for ref in branch_refs]

    branch_status = {
        branch.branch_id: ("unavailable" if branch.unavailable_reason else "collected")
        for branch in branches
    }
    evidence_ids = sorted(item.evidence_id for branch in branches for item in branch.evidence)
    raw_refs_by_digest = {
        item.raw_ref.content_digest: item.raw_ref for branch in branches for item in branch.evidence
    }

    fan_in = _seal(
        RawEvidenceFanIn(
            **_base_envelope(
                state, "RawEvidenceFanIn", parents=[branch.content_digest for branch in branches]
            ),
            branch_status=branch_status,
            evidence_ids=evidence_ids,
            raw_refs=[raw_refs_by_digest[key] for key in sorted(raw_refs_by_digest)],
        )
    )
    ref = _put_envelope(ports, state, fan_in, node_id="A2.70")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_70_fan_stable_branch_identity_write_raw_bytes"]
