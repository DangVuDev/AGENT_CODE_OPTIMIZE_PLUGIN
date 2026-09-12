from __future__ import annotations

from datetime import datetime
from typing import Protocol

from production_optimizer.contracts.registries import RegistryKind, RegistryRecord


class RegistryPort(Protocol):
    """Read access to tenant-scoped, versioned registry records.

    Backs the Lane 1/2 "registration" nodes (repository/feature/owner
    registries in B1.20, collector/analyzer bindings in A2.40/A2.60-A2.64,
    policy bindings in A2.50) that must resolve a named capability without
    the handler itself deciding what is allowed to run. A record's
    `valid_from`/`valid_until` window and `enabled` flag are authoritative;
    adapters must not return an expired or disabled record from `resolve`.
    """

    def resolve(
        self,
        *,
        tenant_id: str,
        registry_kind: RegistryKind,
        record_id: str,
        at: datetime,
    ) -> RegistryRecord | None: ...

    def list_active(
        self,
        *,
        tenant_id: str,
        registry_kind: RegistryKind,
        at: datetime,
    ) -> list[RegistryRecord]: ...
