from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from production_optimizer.application.registry import (
    RegistryConflictError,
    RegistryNotFoundError,
    VersionedRegistry,
)
from production_optimizer.contracts.registries import RegistryKind, RegistryRecord

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _record(version: int, **overrides: object) -> RegistryRecord:
    values: dict[str, object] = {
        "registry_kind": RegistryKind.FEATURE,
        "record_id": "checkout",
        "version": version,
        "tenant_id": "tenant-a",
        "valid_from": NOW - timedelta(days=version),
        "payload": {"feature_id": "checkout"},
    }
    values.update(overrides)
    return RegistryRecord.model_validate(values)


def test_registry_resolves_latest_active_version_with_tenant_isolation() -> None:
    registry = VersionedRegistry([_record(1), _record(2)])

    resolved = registry.resolve(
        tenant_id="tenant-a", kind=RegistryKind.FEATURE, record_id="checkout", at=NOW
    )

    assert resolved.version == 2
    with pytest.raises(RegistryNotFoundError):
        registry.resolve(
            tenant_id="tenant-b", kind=RegistryKind.FEATURE, record_id="checkout", at=NOW
        )


def test_registry_supports_pinned_version_and_validity_window() -> None:
    registry = VersionedRegistry(
        [_record(1), _record(2, valid_until=NOW - timedelta(hours=1))]
    )
    assert registry.resolve(
        tenant_id="tenant-a",
        kind=RegistryKind.FEATURE,
        record_id="checkout",
        at=NOW,
        version=1,
    ).version == 1
    assert registry.resolve(
        tenant_id="tenant-a", kind=RegistryKind.FEATURE, record_id="checkout", at=NOW
    ).version == 1


def test_registry_rejects_duplicates_and_naive_resolution_time() -> None:
    record = _record(1)
    with pytest.raises(RegistryConflictError):
        VersionedRegistry([record, record])
    with pytest.raises(ValueError, match="timezone-aware"):
        VersionedRegistry([record]).resolve(
            tenant_id="tenant-a",
            kind=RegistryKind.FEATURE,
            record_id="checkout",
            at=datetime(2026, 1, 1),
        )
