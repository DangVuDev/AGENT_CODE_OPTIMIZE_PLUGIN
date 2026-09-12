from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Entry:
    value: str
    expires_at: float


class TTLStore:
    """In-memory key/value store where each entry expires after a TTL."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def put(self, key: str, value: str, ttl_seconds: float) -> None:
        self._entries[key] = _Entry(value=value, expires_at=time.monotonic() + ttl_seconds)

    def get(self, key: str) -> str:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._entries[key]
            return None
        return entry.value
