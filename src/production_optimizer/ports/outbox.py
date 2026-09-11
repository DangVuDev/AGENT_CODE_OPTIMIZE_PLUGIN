from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.platform import OutboxRecord


class OutboxPort(Protocol):
    """Transactional at-least-once dispatch boundary for cross-thread handoffs."""

    def publish(self, record: OutboxRecord) -> OutboxRecord: ...

    def claim(self, *, tenant_id: str, limit: int) -> list[OutboxRecord]: ...

    def mark_delivered(self, *, tenant_id: str, event_id: str) -> OutboxRecord: ...

    def mark_failed(
        self, *, tenant_id: str, event_id: str, error_ref: str
    ) -> OutboxRecord: ...
