from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import IntentRecord


class IntentLedger(Protocol):
    def prepare(self, intent: IntentRecord) -> IntentRecord: ...

    def get(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord | None: ...

    def complete(
        self, *, tenant_id: str, idempotency_key: str, output_ref: ArtifactRef
    ) -> IntentRecord: ...

    def mark_unknown(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord: ...
