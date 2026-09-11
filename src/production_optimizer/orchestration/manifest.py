from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from production_optimizer.application.node_contract import SideEffectClass
from production_optimizer.contracts.base import ContractModel

from .catalog import ALL_BUSINESS_NODE_IDS


class NodeReadiness(StrEnum):
    DISABLED = "disabled"
    READY = "ready"


class NodeManifestEntry(ContractModel):
    node_id: str = Field(pattern=r"^(A[1-3]|B[12]|C0)\.[0-9]{2}$")
    stage: str = Field(pattern=r"^(A[1-3]|B[12]|C0)$")
    owner: str = Field(min_length=1)
    readiness: NodeReadiness
    enabled: bool
    side_effect_class: SideEffectClass | None = None
    handler: str | None = None
    definition_of_ready_evidence: list[str] = Field(default_factory=list)


def build_p2_node_manifest() -> tuple[NodeManifestEntry, ...]:
    """Return the P2 fail-closed manifest; business handlers are a P3 concern."""

    return tuple(
        NodeManifestEntry(
            node_id=node_id,
            stage=node_id.split(".", maxsplit=1)[0],
            owner=f"{node_id.split('.', maxsplit=1)[0].lower()}-service",
            readiness=NodeReadiness.DISABLED,
            enabled=False,
        )
        for node_id in sorted(ALL_BUSINESS_NODE_IDS)
    )
