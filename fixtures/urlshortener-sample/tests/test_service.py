from urlshortener.service import UrlShortenerService


def test_shorten_then_resolve_returns_original_url() -> None:
    service = UrlShortenerService()
    result = service.shorten("https://example.com/very/long/path")
    assert service.resolve(result.code) == "https://example.com/very/long/path"


def test_shorten_assigns_distinct_codes() -> None:
    service = UrlShortenerService()
    first = service.shorten("https://a.example.com")
    second = service.shorten("https://b.example.com")
    assert first.code != second.code


def test_resolve_unknown_code_returns_none() -> None:
    service = UrlShortenerService()
    assert service.resolve("does-not-exist") is None
