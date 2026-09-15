from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.platform import DeferredModelCallRecord


class ModelCallDeferralPort(Protocol):
    """Durable retry-later queue for model calls that exhausted provider resilience."""

    def defer(self, record: DeferredModelCallRecord) -> DeferredModelCallRecord: ...

    def get(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord | None: ...

    def claim_due(
        self, *, tenant_id: str, lease_owner: str, lease_seconds: int, limit: int
    ) -> list[DeferredModelCallRecord]: ...

    def mark_succeeded(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord: ...

    def mark_failed(
        self, *, tenant_id: str, deferral_id: str, error_ref: str
    ) -> DeferredModelCallRecord: ...

    def healthcheck(self) -> bool: ...
