from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.platform import (
    DeferredModelCallRecord,
    DeferredModelCallStatus,
    ModelCallFailureRecord,
    ModelCompletionRequest,
    ModelCompletionResult,
)
from production_optimizer.ports.model_deferrals import ModelCallDeferralPort
from production_optimizer.ports.model_provider import ModelProviderPort

# Retryable HTTP statuses: 408 request timeout, 409 conflict (some providers
# use it for concurrent-request limits), 429 rate limit, and every 5xx.
_RETRYABLE_STATUSES = frozenset({408, 409, 429})

# Provider SDKs raise their own exception classes (anthropic.RateLimitError,
# openai.APITimeoutError, google.genai.errors.APIError, httpx.ConnectError,
# ...). Matching on class *name* instead of importing five SDKs keeps this
# adapter dependency-free and correct for providers added later.
_RETRYABLE_NAME_FRAGMENTS = (
    "ratelimit",
    "timeout",
    "connect",
    "overloaded",
    "serviceunavailable",
    "internalserver",
    "apiconnection",
    "remoteprotocol",
)

_DEFAULT_MAX_ATTEMPTS = 4
_DEFAULT_BASE_DELAY_SECONDS = 2.0
_DEFAULT_MAX_DELAY_SECONDS = 30.0
_DEFAULT_MAX_TOTAL_DELAY_SECONDS = 60.0
_DEFAULT_BREAKER_FAILURE_THRESHOLD = 1
_DEFAULT_BREAKER_COOLDOWN_SECONDS = 120.0


def _status_code_of(error: BaseException) -> int | None:
    """Best-effort HTTP status extraction across provider SDK exceptions.

    Anthropic/OpenAI put it on `.status_code`, google-genai on `.code`, and
    httpx-based clients on `.response.status_code`.
    """

    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_retryable(error: BaseException) -> bool:
    """Transient (worth retrying) vs permanent (retrying only wastes quota).

    A 401/403/400 means the key or the request is wrong and will stay wrong,
    so it is surfaced immediately instead of being retried four times.
    """

    status = _status_code_of(error)
    if status is not None:
        return status in _RETRYABLE_STATUSES or 500 <= status <= 599
    name = type(error).__name__.lower()
    return any(fragment in name for fragment in _RETRYABLE_NAME_FRAGMENTS)


class RetryingModelProvider:
    """Wraps any `ModelProviderPort` with bounded exponential backoff.

    Exists because a single 429 or transient 5xx used to abort a whole case:
    `a3_handlers` calls `ports.model.complete()` with no exception handling
    (deliberately — a node should not hide a dead provider), `NodeRuntime`
    marks the intent unknown and re-raises, and with no checkpointer the run
    is lost. Handling *unavailability* is a transport concern, so it belongs
    here rather than in every business node.

    Only transport failures are retried. A malformed *response* (invalid
    JSON) is not an error at this layer — it comes back as a normal
    `ModelCompletionResult` with `valid_json=False`, and the calling node's
    own bounded repair loop owns that case.
    """

    def __init__(
        self,
        inner: ModelProviderPort,
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
        max_delay_seconds: float = _DEFAULT_MAX_DELAY_SECONDS,
        max_total_delay_seconds: float = _DEFAULT_MAX_TOTAL_DELAY_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        random_between: Callable[[float, float], float] = random.uniform,
        on_retry: Callable[[int, float, BaseException], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_delay_seconds < 0 or max_delay_seconds < 0 or max_total_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        self._inner = inner
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._max_total_delay_seconds = max_total_delay_seconds
        self._sleep = sleep
        self._random_between = random_between
        self._on_retry = on_retry

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        total_delay = 0.0
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._inner.complete(request)
            except Exception as error:
                if attempt == self._max_attempts or not is_retryable(error):
                    raise
                delay = self._delay_for(attempt, error)
                if total_delay + delay > self._max_total_delay_seconds:
                    raise
                if self._on_retry is not None:
                    self._on_retry(attempt, delay, error)
                self._sleep(delay)
                total_delay += delay
        raise AssertionError("unreachable: loop either returns or raises")

    def healthcheck(self) -> bool:
        return self._inner.healthcheck()

    def _delay_for(self, attempt: int, error: BaseException) -> float:
        """Honour a provider-supplied `retry-after` when present, else use
        exponential backoff with equal jitter. Equal jitter preserves half of
        the backoff as a minimum wait while randomizing the other half, so an
        overloaded provider is not hit again almost immediately and parallel
        cases still avoid retrying in lockstep."""

        retry_after = _retry_after_seconds(error)
        if retry_after is not None:
            return min(retry_after, self._max_delay_seconds)
        ceiling = min(self._base_delay_seconds * (2 ** (attempt - 1)), self._max_delay_seconds)
        return self._random_between(ceiling / 2.0, ceiling)


@dataclass(frozen=True, slots=True)
class ModelProviderCandidate:
    """One approved provider/model pair the gateway may call."""

    provider_name: str
    provider: ModelProviderPort
    model_id: str


@dataclass(frozen=True, slots=True)
class ModelGatewayEvent:
    """Small operational event emitted by `ResilientModelGateway`.

    This intentionally stays adapter-local instead of becoming a sealed
    domain artifact: model fallback is transport behavior, while A3/S02
    artifacts must remain about product reasoning and implementation plans.
    """

    event_type: str
    provider_name: str
    model_id: str
    detail: str = ""


@dataclass(slots=True)
class _CircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ModelProviderFailure:
    provider_name: str
    model_id: str
    retryable: bool
    error_type: str
    message: str


class DeferredModelCallError(RuntimeError):
    """Raised when no approved model provider can serve the call right now.

    In a durable deployment this is the signal a scheduler/checkpointer would
    convert into "persist exact node state and retry later". In local CLI
    mode there is no durable scheduler, so entrypoints can print a clear
    non-corrupting halt instead of a long provider traceback.
    """

    def __init__(
        self,
        *,
        request_id: str,
        failures: list[ModelProviderFailure],
        retry_after_seconds: float,
        deferral_id: str | None = None,
    ) -> None:
        self.request_id = request_id
        self.failures = failures
        self.retry_after_seconds = retry_after_seconds
        self.deferral_id = deferral_id
        summary = ", ".join(
            f"{failure.provider_name}/{failure.model_id}: "
            f"{failure.error_type}({failure.message})"
            for failure in failures
        )
        super().__init__(
            f"model call deferred for {request_id}; retry after "
            f"{retry_after_seconds:.0f}s; failures: {summary}"
        )


class ResilientModelGateway:
    """Provider failover wrapper for production model calls.

    The immediate retry/backoff behavior remains in `RetryingModelProvider`
    around each individual provider. This gateway sits one level above that:
    it avoids providers whose circuit is open, tries the next approved
    provider when a transient outage survives retries, and raises a typed
    deferred error when no provider is currently usable.
    """

    def __init__(
        self,
        candidates: list[ModelProviderCandidate],
        *,
        failure_threshold: int = _DEFAULT_BREAKER_FAILURE_THRESHOLD,
        cooldown_seconds: float = _DEFAULT_BREAKER_COOLDOWN_SECONDS,
        tenant_id: str | None = None,
        thread_id: str | None = None,
        deferrals: ModelCallDeferralPort | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_event: Callable[[ModelGatewayEvent], None] | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("ResilientModelGateway requires at least one provider candidate")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        self._candidates = candidates
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._tenant_id = tenant_id
        self._thread_id = thread_id
        self._deferrals = deferrals
        self._monotonic = monotonic
        self._now = now
        self._on_event = on_event
        self._circuits = {candidate.provider_name: _CircuitState() for candidate in candidates}

    @property
    def candidates(self) -> tuple[ModelProviderCandidate, ...]:
        return tuple(self._candidates)

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        failures: list[ModelProviderFailure] = []
        skipped_open = 0
        for candidate in self._candidates:
            state = self._circuits[candidate.provider_name]
            if self._is_open(state):
                skipped_open += 1
                self._emit("circuit_open", candidate, state.last_error or "cooldown active")
                continue

            provider_request = request.model_copy(update={"model_id": candidate.model_id})
            try:
                result = candidate.provider.complete(provider_request)
            except Exception as error:
                retryable = is_retryable(error)
                failure = ModelProviderFailure(
                    provider_name=candidate.provider_name,
                    model_id=candidate.model_id,
                    retryable=retryable,
                    error_type=type(error).__name__,
                    message=str(error),
                )
                failures.append(failure)
                if not retryable:
                    self._record_success(candidate)
                    raise
                self._record_failure(candidate, failure)
                self._emit("fallback", candidate, f"{failure.error_type}: {failure.message}")
                continue

            self._record_success(candidate)
            if candidate is not self._candidates[0]:
                self._emit("fallback_success", candidate, f"served {request.idempotency_key}")
            return result

        retry_after = self._next_retry_after_seconds(skipped_open=skipped_open)
        deferral_id = self._persist_deferral(
            request=request,
            failures=failures,
            retry_after_seconds=retry_after,
        )
        raise DeferredModelCallError(
            request_id=request.idempotency_key,
            failures=failures,
            retry_after_seconds=retry_after,
            deferral_id=deferral_id,
        )

    def healthcheck(self) -> bool:
        return any(not self._is_open(self._circuits[c.provider_name]) for c in self._candidates)

    def _record_failure(
        self, candidate: ModelProviderCandidate, failure: ModelProviderFailure
    ) -> None:
        state = self._circuits[candidate.provider_name]
        state.consecutive_failures += 1
        state.last_error = f"{failure.error_type}: {failure.message}"
        if state.consecutive_failures >= self._failure_threshold:
            state.opened_at = self._monotonic()
            self._emit("circuit_opened", candidate, state.last_error)

    def _record_success(self, candidate: ModelProviderCandidate) -> None:
        state = self._circuits[candidate.provider_name]
        if state.consecutive_failures or state.opened_at is not None:
            self._emit("circuit_closed", candidate, "provider recovered")
        state.consecutive_failures = 0
        state.opened_at = None
        state.last_error = None

    def _is_open(self, state: _CircuitState) -> bool:
        if state.opened_at is None:
            return False
        if self._monotonic() - state.opened_at >= self._cooldown_seconds:
            state.opened_at = None
            return False
        return True

    def _next_retry_after_seconds(self, *, skipped_open: int) -> float:
        if skipped_open == 0:
            return self._cooldown_seconds
        open_remaining = [
            max(0.0, self._cooldown_seconds - (self._monotonic() - state.opened_at))
            for state in self._circuits.values()
            if state.opened_at is not None
        ]
        return min(open_remaining) if open_remaining else self._cooldown_seconds

    def _emit(self, event_type: str, candidate: ModelProviderCandidate, detail: str) -> None:
        if self._on_event is None:
            return
        self._on_event(
            ModelGatewayEvent(
                event_type=event_type,
                provider_name=candidate.provider_name,
                model_id=candidate.model_id,
                detail=detail,
            )
        )

    def _persist_deferral(
        self,
        *,
        request: ModelCompletionRequest,
        failures: list[ModelProviderFailure],
        retry_after_seconds: float,
    ) -> str | None:
        if self._deferrals is None or self._tenant_id is None:
            return None
        case_id, node_id = _case_and_node_from_key(request.idempotency_key)
        request_digest = sha256_digest(canonical_json(request))
        deferral_id = _deferral_id(request.idempotency_key)
        now = self._now()
        record = DeferredModelCallRecord(
            tenant_id=self._tenant_id,
            deferral_id=deferral_id,
            case_id=case_id,
            thread_id=self._thread_id,
            node_id=node_id,
            idempotency_key=request.idempotency_key,
            request_digest=request_digest,
            status=DeferredModelCallStatus.SCHEDULED,
            role=request.role,
            prompt_version=request.prompt_version,
            primary_model_id=request.model_id,
            failures=[
                ModelCallFailureRecord(
                    provider_name=failure.provider_name,
                    model_id=failure.model_id,
                    retryable=failure.retryable,
                    error_type=failure.error_type,
                    message=failure.message,
                )
                for failure in failures
            ],
            retry_after_seconds=retry_after_seconds,
            available_at=now + timedelta(seconds=retry_after_seconds),
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        self._deferrals.defer(record)
        return deferral_id


def _retry_after_seconds(error: BaseException) -> float | None:
    response: Any = getattr(error, "response", None)
    headers: Any = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _case_and_node_from_key(idempotency_key: str) -> tuple[str, str]:
    parts = idempotency_key.split(":")
    case_id = parts[0] if parts and parts[0] else "unknown-case"
    node_id = parts[1] if len(parts) > 1 and parts[1] else "unknown-node"
    return case_id, node_id


def _deferral_id(idempotency_key: str) -> str:
    digest = sha256_digest(idempotency_key.encode("utf-8")).removeprefix("sha256:")
    return f"model-call-{digest}"
