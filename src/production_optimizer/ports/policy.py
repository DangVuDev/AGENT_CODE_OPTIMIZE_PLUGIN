from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.platform import PolicyDecision, PolicyRequest


class PolicyPort(Protocol):
    def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...

    def healthcheck(self) -> bool: ...
