from pydantic import Field

from .base import ContractModel


class ArtifactRef(ContractModel):
    """Compact pointer allowed in graph state; never embeds raw artifact data."""

    artifact_type: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    artifact_id: str = Field(min_length=1, max_length=100)
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    uri: str = Field(min_length=1, max_length=2048)
