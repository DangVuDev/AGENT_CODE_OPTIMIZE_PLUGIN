from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.state import OptimizationState


class CaseRepository(Protocol):
    def create(self, state: OptimizationState) -> None: ...

    def get(self, *, tenant_id: str, case_id: str) -> OptimizationState | None: ...

    def set_status(self, *, tenant_id: str, case_id: str, status: str) -> None: ...

    def request_cancellation(self, *, tenant_id: str, case_id: str, actor_id: str) -> None: ...
