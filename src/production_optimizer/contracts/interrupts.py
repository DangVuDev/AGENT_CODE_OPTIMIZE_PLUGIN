from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from .base import ContractModel


class InterruptEnvelope(ContractModel):
    interrupt_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1, max_length=255)
    stage: str = Field(min_length=1)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed_decisions: list[str] = Field(min_length=1)
    required_actor_role: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def expiration_follows_issue(self) -> InterruptEnvelope:
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("interrupt timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("interrupt expiration must follow issue time")
        return self
