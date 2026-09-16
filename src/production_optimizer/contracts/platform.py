from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from .artifacts import ArtifactRef
from .base import ContractModel


class IntentStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    UNKNOWN = "unknown"
    FAILED = "failed"


class ActorContext(ContractModel):
    actor_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    roles: set[str] = Field(min_length=1)
    authenticated_at: datetime


class PolicyRequest(ContractModel):
    decision_type: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    facts: dict[str, Any]


class PolicyDecision(ContractModel):
    allowed: bool
    decision: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    reasons: list[str]


class IntentRecord(ContractModel):
    tenant_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    status: IntentStatus
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_ref: ArtifactRef | None = None
    updated_at: datetime


class WorkerJob(ContractModel):
    job_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    input_refs: list[ArtifactRef]
    capability: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    secret_refs: list[str] = Field(default_factory=list)


class WorkerReceipt(ContractModel):
    job_id: str
    accepted: bool
    lease_id: str | None = None
    expires_at: datetime | None = None


class ModelRole(StrEnum):
    """Which capacity a model call is made in.

    Carrying this on the request itself (not just as an adapter-side
    convention) is what makes "the generator cannot judge itself" (A3
    playbook) mechanically checkable: a judge call is a structurally
    separate `ModelCompletionRequest`, never a continuation of a generator
    conversation.
    """

    GENERATOR = "generator"
    JUDGE = "judge"


class ModelMessage(ContractModel):
    role: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ModelCompletionRequest(ContractModel):
    role: ModelRole
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    messages: list[ModelMessage] = Field(min_length=1)
    response_schema: dict[str, Any]
    max_output_tokens: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1)


class ModelCompletionResult(ContractModel):
    request_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    raw_text: str
    parsed_json: dict[str, Any] | None = None
    valid_json: bool
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    stop_reason: str = Field(min_length=1)


class TelemetryEvent(ContractModel):
    case_id: str
    thread_id: str
    node_id: str
    attempt: int = Field(ge=1)
    result_code: str
    attributes: dict[str, str | int | float | bool]


class OutboxRecord(ContractModel):
    """Transactional dispatch record backing `optimizer_control.outbox`."""

    event_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    payload_ref: str = Field(min_length=1, max_length=2048)
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    available_at: datetime
    delivered_at: datetime | None = None
    attempts: int = Field(ge=0)
    last_error_ref: str | None = None
