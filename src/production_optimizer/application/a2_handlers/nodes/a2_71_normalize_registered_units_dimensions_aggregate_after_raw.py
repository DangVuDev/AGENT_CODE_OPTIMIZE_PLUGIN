# pyright: reportPrivateUsage=false
"""Implementation of business node A2.71."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import (
    BranchEvidenceRefs,
    CollectorPlan,
    EvidenceItem,
    NormalizedEvidenceSet,
    RawEvidenceFanIn,
)
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _FAN_IN_BRANCH_IDS,
    _base_envelope,
    _normalize_observation,
    _put_envelope,
    _read_model,
    _require_branch_ref,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_a2_71_normalize_registered_units_dimensions_aggregate_after_raw(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Normalize the evidence A2.70 fanned in.

    Every unit A2.60/A2.61/A2.64 currently emit (`exit_code`, `files_mapped`)
    is already in canonical form — there is no ms->s style conversion to do
    yet — so normalization here means: verify every fanned-in evidence_id
    still resolves to a real item in its owning branch, and reject (not
    silently pass through) any unit this node does not recognize. An
    evidence_id present in `RawEvidenceFanIn` but missing from its branch, or
    carrying an unrecognized unit, is a `conversion_failures` entry, never a
    fabricated pass-through.
    """

    fan_in = _read_model(ports, state, _require_ref(state, "RawEvidenceFanIn"), RawEvidenceFanIn)
    plan = _read_model(ports, state, _require_ref(state, "CollectorPlan"), CollectorPlan)
    branches = [
        _read_model(ports, state, _require_branch_ref(state, node_id), BranchEvidenceRefs)
        for node_id in _FAN_IN_BRANCH_IDS
    ]
    by_id = {item.evidence_id: item for branch in branches for item in branch.evidence}

    normalized: list[EvidenceItem] = []
    failures: list[str] = []
    transformations: dict[str, str] = {}
    binding_by_requirement = {binding.requirement_id: binding for binding in plan.bindings}
    tenant_id = _required_state_str(state, "tenant_id")
    for evidence_id in fan_in.evidence_ids:
        item = by_id.get(evidence_id)
        if item is None:
            failures.append(f"{evidence_id}: missing from branch evidence (fan-in integrity gap)")
            continue
        binding = (
            binding_by_requirement.get(item.requirement_id)
            if item.requirement_id is not None
            else None
        )
        try:
            value, unit, transformation = _normalize_observation(item, binding)
        except ValueError as exc:
            failures.append(f"{evidence_id}: {exc}")
            continue
        normalized_content = canonical_json(
            {
                "evidence_id": item.evidence_id,
                "requirement_id": item.requirement_id,
                "metric_id": item.metric_id,
                "value": value,
                "unit": unit,
                "transformation_id": transformation,
                "raw_digest": item.raw_ref.content_digest,
            }
        )
        normalized_ref = ports.artifacts.put_blob(
            tenant_id=tenant_id,
            content=normalized_content,
            content_digest=sha256_digest(normalized_content),
            media_type="application/json",
        )
        normalized.append(
            item.model_copy(
                update={
                    "normalized_ref": normalized_ref,
                    "value": value,
                    "unit": unit,
                    "transformation_id": transformation,
                }
            )
        )
        transformations[item.evidence_id] = transformation

    normalized_set = _seal(
        NormalizedEvidenceSet(
            **_base_envelope(state, "NormalizedEvidenceSet", parents=[fan_in.content_digest]),
            evidence=normalized,
            conversion_failures=failures,
            transformation_versions=transformations,
        )
    )
    ref = _put_envelope(ports, state, normalized_set, node_id="A2.71")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_71_normalize_registered_units_dimensions_aggregate_after_raw"]
