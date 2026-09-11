from datetime import datetime

from pydantic import Field

from .artifacts import ArtifactRef
from .base import ContractModel


class EventRef(ContractModel):
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    occurred_at: datetime
    artifact_ref: ArtifactRef
