from urlshortener.service import UrlShortenerService


def test_shorten_benchmark(benchmark) -> None:
    service = UrlShortenerService()
    benchmark(service.shorten, "https://example.com/benchmark-target")
