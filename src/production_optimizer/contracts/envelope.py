from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel

Digest = str


class ProducerIdentity(ContractModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)


class ArtifactEnvelope(ContractModel):
    artifact_type: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    artifact_id: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    created_at: datetime
    producer: ProducerIdentity
    policy_versions: dict[str, str] = Field(default_factory=dict)
    content_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    parent_digests: list[Digest] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_envelope(self) -> ArtifactEnvelope:
        if self.created_at.tzinfo is None:
            raise ValueError("artifact timestamp must be timezone-aware")
        if len(self.parent_digests) != len(set(self.parent_digests)):
            raise ValueError("parent digests must be unique")
        if any(not item.startswith("sha256:") or len(item) != 71 for item in self.parent_digests):
            raise ValueError("parent digests must be SHA-256 identities")
        return self
