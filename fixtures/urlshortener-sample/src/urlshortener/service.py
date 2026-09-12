from __future__ import annotations

import json
from dataclasses import dataclass

from .encoding import encode
from .store import TTLStore

_DEFAULT_TTL_SECONDS = 3600.0


@dataclass
class ShortenResult:
    code: str
    url: str


class UrlShortenerService:
    def __init__(self, store: TTLStore | None = None) -> None:
        self._store = store or TTLStore()
        self._next_id = 1

    def shorten(self, url: str, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> ShortenResult:
        code = encode(self._next_id)
        self._next_id += 1
        self._store.put(code, url, ttl_seconds)
        return ShortenResult(code=code, url=url)

    def resolve(self, code: str) -> str | None:
        return self._store.get(code)
