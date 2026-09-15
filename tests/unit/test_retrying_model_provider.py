from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from production_optimizer.adapters.production.retrying_model_provider import (
    DeferredModelCallError,
    ModelProviderCandidate,
    ResilientModelGateway,
    RetryingModelProvider,
    is_retryable,
)
from production_optimizer.contracts.platform import (
    DeferredModelCallRecord,
    ModelCompletionRequest,
    ModelCompletionResult,
    ModelMessage,
    ModelRole,
)


class _RateLimitError(Exception):
    """Shaped like anthropic/openai SDK errors: carries `.status_code`."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class _GeminiStyleError(Exception):
    """Shaped like google-genai errors: carries `.code`."""

    def __init__(self, code: int) -> None:
        super().__init__(f"code {code}")
        self.code = code


class _Headers:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str) -> str | None:
        return self._values.get(key)


class _Response:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = _Headers(headers or {})


class _HttpxStyleError(Exception):
    """Shaped like httpx.HTTPStatusError: carries `.response.status_code`."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"http {status_code}")
        self.response = _Response(status_code, headers)


class APITimeoutError(Exception):
    """No status code at all -- classified by class name, like real SDKs."""


class _FakeProvider:
    def __init__(self, *, failures: list[BaseException]) -> None:
        self.failures = failures
        self.calls = 0

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ModelCompletionResult(
            request_id=request.idempotency_key,
            model_id=request.model_id,
            model_version="fake-1",
            raw_text="{}",
            parsed_json={},
            valid_json=True,
            input_tokens=1,
            output_tokens=1,
            stop_reason="end_turn",
        )

    def healthcheck(self) -> bool:
        return True


class _RecordingProvider:
    def __init__(self, *, failures: list[BaseException] | None = None) -> None:
        self.failures = failures or []
        self.calls: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.calls.append(request)
        if self.failures:
            raise self.failures.pop(0)
        return ModelCompletionResult(
            request_id=request.idempotency_key,
            model_id=request.model_id,
            model_version=f"{request.model_id}-version",
            raw_text="{}",
            parsed_json={},
            valid_json=True,
            input_tokens=1,
            output_tokens=1,
            stop_reason="end_turn",
        )

    def healthcheck(self) -> bool:
        return True


class _RecordingDeferrals:
    def __init__(self) -> None:
        self.records: list[DeferredModelCallRecord] = []

    def defer(self, record: DeferredModelCallRecord) -> DeferredModelCallRecord:
        self.records.append(record)
        return record

    def get(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord | None:
        return next(
            (
                record
                for record in self.records
                if record.tenant_id == tenant_id and record.deferral_id == deferral_id
            ),
            None,
        )

    def claim_due(
        self, *, tenant_id: str, lease_owner: str, lease_seconds: int, limit: int
    ) -> list[DeferredModelCallRecord]:
        del tenant_id, lease_owner, lease_seconds, limit
        return []

    def mark_succeeded(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord:
        record = self.get(tenant_id=tenant_id, deferral_id=deferral_id)
        if record is None:
            raise ValueError(deferral_id)
        return record

    def mark_failed(
        self, *, tenant_id: str, deferral_id: str, error_ref: str
    ) -> DeferredModelCallRecord:
        del error_ref
        record = self.get(tenant_id=tenant_id, deferral_id=deferral_id)
        if record is None:
            raise ValueError(deferral_id)
        return record

    def healthcheck(self) -> bool:
        return True


def _request() -> ModelCompletionRequest:
    return ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id="test-model",
        prompt_version="test-v1",
        messages=[ModelMessage(role="user", content="hello")],
        response_schema={"type": "object"},
        max_output_tokens=100,
        idempotency_key="CASE-1:A3.40:v1",
    )


def _no_sleep(_seconds: float) -> None:
    return None


def test_retryable_classification_by_status_code() -> None:
    assert is_retryable(_RateLimitError(429))
    assert is_retryable(_RateLimitError(503))
    assert is_retryable(_GeminiStyleError(429))
    assert is_retryable(_HttpxStyleError(500))
    # Permanent failures must not burn retries: a bad key stays bad.
    assert not is_retryable(_RateLimitError(401))
    assert not is_retryable(_RateLimitError(400))
    assert not is_retryable(_GeminiStyleError(403))


def test_retryable_classification_by_exception_name() -> None:
    assert is_retryable(APITimeoutError("timed out"))
    assert not is_retryable(ValueError("bad argument"))


def test_recovers_after_transient_rate_limit() -> None:
    provider = _FakeProvider(failures=[_RateLimitError(429), _RateLimitError(503)])
    retrying = RetryingModelProvider(provider, sleep=_no_sleep)

    result = retrying.complete(_request())

    assert result.valid_json
    assert provider.calls == 3, "expected two failures then one success"


def test_permanent_error_is_raised_immediately() -> None:
    provider = _FakeProvider(failures=[_RateLimitError(401), _RateLimitError(401)])
    retrying = RetryingModelProvider(provider, sleep=_no_sleep)

    with pytest.raises(_RateLimitError):
        retrying.complete(_request())

    assert provider.calls == 1, "a 401 must not be retried"


def test_gives_up_after_max_attempts_and_reraises() -> None:
    provider = _FakeProvider(failures=[_RateLimitError(429) for _ in range(10)])
    retrying = RetryingModelProvider(provider, max_attempts=3, sleep=_no_sleep)

    with pytest.raises(_RateLimitError):
        retrying.complete(_request())

    assert provider.calls == 3


def test_honours_retry_after_header() -> None:
    slept: list[float] = []
    provider = _FakeProvider(failures=[_HttpxStyleError(429, {"retry-after": "7"})])
    retrying = RetryingModelProvider(provider, sleep=slept.append)

    retrying.complete(_request())

    assert slept == [7.0]


def test_backoff_is_bounded_by_max_delay() -> None:
    slept: list[float] = []
    provider = _FakeProvider(failures=[_RateLimitError(429) for _ in range(3)])
    retrying = RetryingModelProvider(
        provider,
        max_attempts=4,
        base_delay_seconds=100.0,
        max_delay_seconds=5.0,
        sleep=slept.append,
        random_between=lambda lower, _upper: lower,
    )

    retrying.complete(_request())

    assert len(slept) == 3
    assert slept == [2.5, 2.5, 2.5]


def test_equal_jitter_never_retries_below_half_the_exponential_ceiling() -> None:
    slept: list[float] = []
    bounds: list[tuple[float, float]] = []
    provider = _FakeProvider(failures=[_RateLimitError(503) for _ in range(3)])

    def choose_lower_bound(lower: float, upper: float) -> float:
        bounds.append((lower, upper))
        return lower

    RetryingModelProvider(
        provider,
        max_attempts=4,
        base_delay_seconds=2.0,
        sleep=slept.append,
        random_between=choose_lower_bound,
    ).complete(_request())

    assert bounds == [(1.0, 2.0), (2.0, 4.0), (4.0, 8.0)]
    assert slept == [1.0, 2.0, 4.0]


def test_total_delay_budget_stops_before_an_excessive_sleep() -> None:
    slept: list[float] = []
    provider = _FakeProvider(failures=[_RateLimitError(503) for _ in range(10)])
    retrying = RetryingModelProvider(
        provider,
        max_attempts=10,
        base_delay_seconds=2.0,
        max_total_delay_seconds=3.0,
        sleep=slept.append,
        random_between=lambda _lower, upper: upper,
    )

    with pytest.raises(_RateLimitError):
        retrying.complete(_request())

    assert slept == [2.0]
    assert provider.calls == 2


def test_invalid_json_response_is_not_retried() -> None:
    """A malformed *response* is the calling node's bounded-repair concern,
    not a transport failure -- retrying it here would double-charge tokens."""

    class _InvalidJsonProvider:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
            self.calls += 1
            return ModelCompletionResult(
                request_id=request.idempotency_key,
                model_id=request.model_id,
                model_version="fake-1",
                raw_text="not json",
                parsed_json=None,
                valid_json=False,
                input_tokens=1,
                output_tokens=1,
                stop_reason="end_turn",
            )

        def healthcheck(self) -> bool:
            return True

    provider = _InvalidJsonProvider()
    result = RetryingModelProvider(provider, sleep=_no_sleep).complete(_request())

    assert result.valid_json is False
    assert provider.calls == 1


def test_healthcheck_delegates() -> None:
    provider: Any = _FakeProvider(failures=[])
    assert RetryingModelProvider(provider, sleep=_no_sleep).healthcheck() is True


def test_resilient_gateway_falls_back_to_next_approved_provider() -> None:
    primary = _RecordingProvider(failures=[_RateLimitError(503)])
    fallback = _RecordingProvider()
    gateway = ResilientModelGateway(
        [
            ModelProviderCandidate("primary", primary, "primary-model"),
            ModelProviderCandidate("fallback", fallback, "fallback-model"),
        ]
    )

    result = gateway.complete(_request())

    assert result.model_id == "fallback-model"
    assert primary.calls[0].model_id == "primary-model"
    assert fallback.calls[0].model_id == "fallback-model"


def test_resilient_gateway_opens_circuit_and_defers_until_cooldown() -> None:
    now = 10.0

    def monotonic() -> float:
        return now

    provider = _RecordingProvider(failures=[_RateLimitError(503), _RateLimitError(503)])
    gateway = ResilientModelGateway(
        [ModelProviderCandidate("primary", provider, "primary-model")],
        cooldown_seconds=30.0,
        monotonic=monotonic,
    )

    with pytest.raises(DeferredModelCallError) as first:
        gateway.complete(_request())
    with pytest.raises(DeferredModelCallError) as second:
        gateway.complete(_request())

    assert len(provider.calls) == 1
    assert first.value.retry_after_seconds == 30.0
    assert second.value.retry_after_seconds == 30.0


def test_resilient_gateway_persists_deferred_model_call_record() -> None:
    fixed_now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    provider = _RecordingProvider(failures=[_RateLimitError(503)])
    deferrals = _RecordingDeferrals()
    gateway = ResilientModelGateway(
        [ModelProviderCandidate("primary", provider, "primary-model")],
        tenant_id="TENANT-1",
        thread_id="THREAD-CASE-1",
        deferrals=deferrals,
        cooldown_seconds=30.0,
        now=lambda: fixed_now,
    )

    with pytest.raises(DeferredModelCallError) as error:
        gateway.complete(_request())

    assert error.value.deferral_id is not None
    assert len(deferrals.records) == 1
    record = deferrals.records[0]
    assert record.deferral_id == error.value.deferral_id
    assert record.tenant_id == "TENANT-1"
    assert record.case_id == "CASE-1"
    assert record.thread_id == "THREAD-CASE-1"
    assert record.node_id == "A3.40"
    assert record.idempotency_key == "CASE-1:A3.40:v1"
    assert record.request_digest.startswith("sha256:")
    assert record.available_at == fixed_now.replace(second=35)
    assert [(failure.provider_name, failure.model_id) for failure in record.failures] == [
        ("primary", "primary-model")
    ]


def test_resilient_gateway_retries_provider_after_cooldown() -> None:
    now = 10.0

    def monotonic() -> float:
        return now

    provider = _RecordingProvider(failures=[_RateLimitError(503)])
    gateway = ResilientModelGateway(
        [ModelProviderCandidate("primary", provider, "primary-model")],
        cooldown_seconds=30.0,
        monotonic=monotonic,
    )

    with pytest.raises(DeferredModelCallError):
        gateway.complete(_request())
    now = 41.0
    result = gateway.complete(_request())

    assert result.valid_json
    assert len(provider.calls) == 2


def test_resilient_gateway_does_not_fallback_on_permanent_error() -> None:
    primary = _RecordingProvider(failures=[_RateLimitError(401)])
    fallback = _RecordingProvider()
    gateway = ResilientModelGateway(
        [
            ModelProviderCandidate("primary", primary, "primary-model"),
            ModelProviderCandidate("fallback", fallback, "fallback-model"),
        ]
    )

    with pytest.raises(_RateLimitError):
        gateway.complete(_request())

    assert len(primary.calls) == 1
    assert fallback.calls == []
