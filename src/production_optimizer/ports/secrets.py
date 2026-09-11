from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol


class SecretsBroker(Protocol):
    def lease(
        self, *, tenant_id: str, secret_ref: str, purpose: str, ttl_seconds: int
    ) -> AbstractContextManager[str]: ...
