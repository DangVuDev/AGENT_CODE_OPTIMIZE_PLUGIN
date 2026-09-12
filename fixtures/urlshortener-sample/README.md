# urlshortener-sample

A tiny URL-shortener service: base62 ID encoding, an in-memory store with
TTL expiry, and a service layer that ties them together. Used as a fixture
target codebase for Lane 1 (A1/A2/A3) — real enough to produce genuine
lint/type/test/benchmark signal, small enough to read in one sitting.

## Running it yourself

```
python -m pytest              # unit tests (one intentionally fails, see below)
python -m ruff check .        # lint
python -m mypy .              # type check (one intentional error, see below)
python -m pytest --benchmark-only  # benchmark
```

## Known, intentional issues (for Lane 1 to discover, not to "fix" here)

- `encoding.decode(encoding.encode(0))` does not round-trip (off-by-one at
  the empty-digits case) — one real failing test.
- `service.py` has an unused import — one real ruff finding.
- `store.py`'s `get` has a wrong return type annotation — one real mypy
  finding.
