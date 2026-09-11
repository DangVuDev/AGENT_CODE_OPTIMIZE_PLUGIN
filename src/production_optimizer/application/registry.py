from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from types import MappingProxyType

from production_optimizer.contracts.registries import RegistryKind, RegistryRecord


class RegistryConflictError(ValueError):
    pass


class RegistryNotFoundError(LookupError):
    pass


class VersionedRegistry:
    """Immutable registry view used consistently by A1-A3, B1-B2 and C0."""

    def __init__(self, records: Iterable[RegistryRecord]) -> None:
        indexed: dict[tuple[str, RegistryKind, str, int], RegistryRecord] = {}
        for record in records:
            key = (record.tenant_id, record.registry_kind, record.record_id, record.version)
            if key in indexed:
                raise RegistryConflictError(f"duplicate registry record: {key!r}")
            indexed[key] = record
        self._records = MappingProxyType(indexed)

    def resolve(
        self,
        *,
        tenant_id: str,
        kind: RegistryKind,
        record_id: str,
        at: datetime,
        version: int | None = None,
    ) -> RegistryRecord:
        if at.tzinfo is None:
            raise ValueError("registry resolution time must be timezone-aware")
        candidates = [
            record
            for (tenant, record_kind, identity, _), record in self._records.items()
            if tenant == tenant_id
            and record_kind is kind
            and identity == record_id
            and record.enabled
            and record.valid_from <= at
            and (record.valid_until is None or at < record.valid_until)
            and (version is None or record.version == version)
        ]
        if not candidates:
            raise RegistryNotFoundError(
                f"no active {kind.value} registration {record_id!r} for tenant {tenant_id!r}"
            )
        return max(candidates, key=lambda item: item.version)

