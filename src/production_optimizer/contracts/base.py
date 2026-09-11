from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Strict base for checkpoint-safe and artifact-reference contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
