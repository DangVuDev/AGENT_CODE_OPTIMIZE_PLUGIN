import time

from urlshortener.store import TTLStore


def test_put_and_get_within_ttl() -> None:
    store = TTLStore()
    store.put("abc", "https://example.com", ttl_seconds=60)
    assert store.get("abc") == "https://example.com"


def test_get_missing_key_returns_none() -> None:
    store = TTLStore()
    assert store.get("missing") is None


def test_get_expired_key_returns_none() -> None:
    store = TTLStore()
    store.put("abc", "https://example.com", ttl_seconds=0.01)
    time.sleep(0.05)
    assert store.get("abc") is None
