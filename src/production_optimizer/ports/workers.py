from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import WorkerJob, WorkerReceipt


class WorkerBroker(Protocol):
    def submit(self, job: WorkerJob) -> WorkerReceipt: ...

    def cancel(self, *, job_id: str, reason: str) -> bool: ...

    def reconcile(self, *, job_id: str, idempotency_key: str) -> ArtifactRef | None: ...
