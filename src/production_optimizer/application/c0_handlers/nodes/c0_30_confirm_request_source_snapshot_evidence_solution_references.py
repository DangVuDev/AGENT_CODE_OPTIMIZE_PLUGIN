# pyright: reportPrivateUsage=false
"""Implementation of business node C0.30."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.c0 import DigestChainLink
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _DIGEST_LINKS,
    _MODEL_BY_TYPE,
    _REQUIRED_ARTIFACTS,
    _read_model,
    _require_ref,
)


def handle_c0_30_confirm_request_source_snapshot_evidence_solution_references(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real digest-chain verification (BR-C0-002): re-read every required
    artifact and compare each `*_digest` field against the actual sealed
    parent's `content_digest`, not a hardcoded `linked=True`."""

    refs = {
        artifact_type: _require_ref(state, artifact_type)
        for artifact_type, _ in _REQUIRED_ARTIFACTS
    }
    links: list[DigestChainLink] = []
    for child_type, field_name, parent_type in _DIGEST_LINKS:
        child_model = _read_model(ports, state, refs[child_type], _MODEL_BY_TYPE[child_type])
        expected_parent_digest = refs[parent_type].content_digest
        actual_value = cast("str", getattr(child_model, field_name))
        links.append(
            DigestChainLink(
                artifact_type=child_type,
                artifact_digest=refs[child_type].content_digest,
                expected_parent_digest=expected_parent_digest,
                linked=(actual_value == expected_parent_digest),
            )
        )
    return NodeExecution(updates={"c0_digest_chain": links})


__all__ = ["handle_c0_30_confirm_request_source_snapshot_evidence_solution_references"]
