from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .artifacts import ArtifactRef
from .base import ContractModel
from .state import OptimizationState


class StartCommandType(StrEnum):
    CREATE_MANUAL_CASE = "CreateManualCase"
    START_DISCOVERY_SCAN = "StartDiscoveryScan"
    START_QUALIFIED_CASE = "StartQualifiedCase"


class StartWorkflowCommand(ContractModel):
    """Authenticated, digest-bound command accepted by the root graph."""

    command_id: str = Field(min_length=1, max_length=100)
    command_type: StartCommandType
    tenant_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    thread_id: str = Field(min_length=1, max_length=255)
    actor_id: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    payload_ref: ArtifactRef
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_lifetime(self) -> StartWorkflowCommand:
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("command timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("command expiration must follow issue time")
        return self

    @property
    def entrypoint(self) -> Literal["manual", "discovery", "qualified"]:
        routes: dict[StartCommandType, Literal["manual", "discovery", "qualified"]] = {
            StartCommandType.CREATE_MANUAL_CASE: "manual",
            StartCommandType.START_DISCOVERY_SCAN: "discovery",
            StartCommandType.START_QUALIFIED_CASE: "qualified",
        }
        return routes[self.command_type]

    def initial_state(self) -> OptimizationState:
        entrypoint = self.entrypoint
        return OptimizationState(
            case_id=self.case_id,
            thread_id=self.thread_id,
            tenant_id=self.tenant_id,
            lane="manual" if entrypoint == "manual" else "automatic",
            entrypoint=entrypoint,
            artifact_refs=[self.payload_ref],
        )


class ResumeInterruptCommand(ContractModel):
    command_id: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    thread_id: str = Field(min_length=1, max_length=255)
    interrupt_id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=255)
    actor_roles: set[str] = Field(min_length=1)
    decision: str = Field(min_length=1, max_length=100)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1, max_length=100)
    issued_at: datetime


class CancelRunCommand(ContractModel):
    command_id: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    thread_id: str = Field(min_length=1, max_length=255)
    actor_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=1000)
    issued_at: datetime
