from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.a2 import SourceAcquisitionRequest, SourceMaterialization


class SourceProviderPort(Protocol):
    """Materialize any registered source kind into a bounded local workspace.

    Git, archive, object-store, database-schema, container-image, and future
    providers implement this protocol. The A2 core only consumes the returned
    materialization and never embeds provider-specific acquisition logic.
    """

    def acquire(self, request: SourceAcquisitionRequest) -> SourceMaterialization: ...

    def healthcheck(self) -> bool: ...


__all__ = ["SourceProviderPort"]
