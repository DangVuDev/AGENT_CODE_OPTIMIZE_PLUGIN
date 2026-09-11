from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.platform import ActorContext


class IdentityPort(Protocol):
    def authenticate(self, credential_reference: str) -> ActorContext: ...

    def authorize(self, actor: ActorContext, *, action: str, resource: str) -> bool: ...
