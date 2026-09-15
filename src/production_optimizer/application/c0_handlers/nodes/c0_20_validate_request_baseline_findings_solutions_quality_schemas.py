# pyright: reportPrivateUsage=false
"""Implementation of business node C0.20."""

from __future__ import annotations

from pydantic import ValidationError

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.c0 import SchemaValidationResult
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _REQUIRED_ARTIFACTS,
    _read_model,
    _require_ref,
)


def handle_c0_20_validate_request_baseline_findings_solutions_quality_schemas(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real schema validation: actually parse each required artifact through
    its Pydantic model rather than trusting the stored `schema_version`."""

    results: list[SchemaValidationResult] = []
    for artifact_type, model_cls in _REQUIRED_ARTIFACTS:
        ref = _require_ref(state, artifact_type)
        try:
            _read_model(ports, state, ref, model_cls)
        except ValidationError as exc:
            results.append(
                SchemaValidationResult(
                    artifact_type=artifact_type,
                    schema_version=ref.schema_version,
                    valid=False,
                    errors=[str(exc)],
                )
            )
            continue
        results.append(
            SchemaValidationResult(
                artifact_type=artifact_type, schema_version=ref.schema_version, valid=True
            )
        )
    return NodeExecution(updates={"c0_schema_results": results})


__all__ = ["handle_c0_20_validate_request_baseline_findings_solutions_quality_schemas"]
