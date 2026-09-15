# pyright: reportPrivateUsage=false
"""Implementation of business node A1.71."""

from __future__ import annotations

from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import (
    CriterionSet,
    EvidenceRequirement,
    EvidenceRequirementSet,
    GuardrailSet,
    WorkloadIdentity,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _metric_profile,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a1_71_map_every_criterion_guardrail_acceptable_source_types(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    criteria = _read_model(ports, state, _require_ref(state, "CriterionSet"), CriterionSet)
    guardrails = _read_model(ports, state, _require_ref(state, "GuardrailSet"), GuardrailSet)
    workload = _read_model(ports, state, _require_ref(state, "WorkloadIdentity"), WorkloadIdentity)
    missing = [*criteria.missing_fields, *guardrails.missing_fields, *workload.missing_fields]
    requirements: list[EvidenceRequirement] = []
    if workload.workload is not None:
        for criterion in criteria.criteria:
            metric = _metric_profile(
                criterion.metric_id, unit=criterion.unit, direction=criterion.direction
            )
            requirements.append(
                EvidenceRequirement(
                    requirement_id=f"evidence-{criterion.criterion_id}",
                    criterion_id=criterion.criterion_id,
                    accepted_source_types=cast("set[str]", metric["source_types"]),
                    # The declared measurement protocol is the single source
                    # of truth for how many samples a criterion needs -- a
                    # second hardcoded constant here could silently disagree
                    # with the `WorkloadContract` the case was approved under.
                    # A2.90 relaxes this to one observation for evidence that
                    # is a deterministic verdict rather than a sample (see
                    # `_effective_minimum_samples`).
                    minimum_samples=max(
                        workload.workload.repetitions,
                        cast("int", metric["minimum_samples"]),
                    ),
                    mandatory=True,
                    metric_id=criterion.metric_id,
                    canonical_unit=cast("str", metric["canonical_unit"]),
                    aggregation=criterion.aggregation,
                    required_dimensions={"environment_id", "workload_id"},
                )
            )
        requirements.extend(
            EvidenceRequirement(
                requirement_id=f"guardrail-{guardrail.guardrail_id}",
                criterion_id=guardrail.guardrail_id,
                accepted_source_types=cast(
                    "set[str]",
                    _metric_profile(guardrail.metric_id, unit=guardrail.unit)["source_types"],
                ),
                minimum_samples=1,
                mandatory=True,
                metric_id=guardrail.metric_id,
                canonical_unit=guardrail.unit,
                aggregation="verdict",
                required_dimensions={"environment_id"},
            )
            for guardrail in guardrails.guardrails
        )
    elif "workload_id" not in missing:
        missing.append("workload_id")
    artifact = _seal(
        EvidenceRequirementSet(
            **_base_envelope(
                state,
                "EvidenceRequirementSet",
                parents=[
                    criteria.content_digest,
                    guardrails.content_digest,
                    workload.content_digest,
                ],
            ),
            evidence_requirements=requirements,
            missing_fields=sorted(set(missing)),
        )
    )
    ref = _put_envelope(ports, state, artifact, node_id="A1.71")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_71_map_every_criterion_guardrail_acceptable_source_types"]
