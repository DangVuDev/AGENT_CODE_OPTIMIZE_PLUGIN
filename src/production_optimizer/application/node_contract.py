from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from production_optimizer.contracts.base import ContractModel
from production_optimizer.contracts.errors import ErrorCode


class SideEffectClass(StrEnum):
    PURE = "pure"
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    EXTERNAL_JOB = "external_job"


def _empty_error_codes() -> set[ErrorCode]:
    return set()


class NodeSpec(ContractModel):
    """Universal operating contract for future graph nodes.

    Defining this model does not register or implement a business node.
    """

    node_id: str = Field(min_length=1)
    business_task_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    input_contract: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    supported_schema_majors: set[int] = Field(min_length=1)
    idempotency_key_version: str = Field(min_length=1)
    side_effect_class: SideEffectClass
    timeout_seconds: int = Field(gt=0)
    max_attempts: int = Field(ge=1, le=10)
    retryable_errors: set[ErrorCode] = Field(default_factory=_empty_error_codes)
    allowed_routes: set[str] = Field(min_length=1)
    runbook: str = Field(min_length=1)
    slo: str = Field(min_length=1)

    @model_validator(mode="after")
    def pure_nodes_do_not_retry(self) -> NodeSpec:
        if self.side_effect_class == SideEffectClass.PURE and self.max_attempts != 1:
            raise ValueError("pure deterministic nodes must be recomputed, not retried internally")
        return self
