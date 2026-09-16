from __future__ import annotations


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


def is_retryable(error: BaseException) -> bool:
    """Transient (worth trying again later) vs permanent (never will work).

    A 401/403/400 means the key or the request is wrong and will stay wrong.
    This is advisory only: nothing in this codebase automatically retries a
    model call -- see `ModelProviderError`'s own docstring for why -- so this
    just tells the human/caller whether trying again later is plausible.
    """

    status = _status_code_of(error)
    if status is not None:
        return status in _RETRYABLE_STATUSES or 500 <= status <= 599
    name = type(error).__name__.lower()
    return any(fragment in name for fragment in _RETRYABLE_NAME_FRAGMENTS)


class ModelProviderError(RuntimeError):
    """The one error a `ModelProviderPort.complete()` call can raise.

    There used to be a retry-with-backoff wrapper and a multi-provider
    failover gateway (circuit breaker, deferred-call scheduler) sitting
    between the business node and the raw SDK call. That machinery hid the
    real failure behind fixed attempt counts/sleeps that did not actually
    fix anything the provider couldn't fix itself, and it made "the model
    call failed" reach the caller as one of several different exception
    types depending on which layer gave up. This adapter now fails fast and
    reports plainly instead: exactly one exception type, carrying enough
    structure (`provider_name`, `model_id`, `error_type`, `retryable`) for a
    caller to log or display the failure, and a `user_message()` for a
    plain-language one-liner. Deciding whether and when to try again is left
    to whatever actually calls `complete()` -- a human re-running the CLI, or
    a future retry policy that lives at the call site, not hidden in here.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        cause: BaseException,
    ) -> None:
        self.provider_name = provider_name
        self.model_id = model_id
        self.error_type = type(cause).__name__
        self.message = str(cause)
        self.retryable = is_retryable(cause)
        super().__init__(
            f"{provider_name}/{model_id} model call failed: {self.error_type}({self.message})"
        )

    def user_message(self) -> str:
        """A short, plain-language explanation safe to print as-is."""

        if self.retryable:
            return (
                f"The model provider ({self.provider_name}/{self.model_id}) failed with a "
                "transient-looking error (rate limit, timeout, or upstream outage). Nothing "
                "was corrupted; wait a moment and re-run the command, or configure a "
                "different provider/model (see .env.example)."
            )
        return (
            f"The model provider ({self.provider_name}/{self.model_id}) failed with an error "
            "that will reproduce every time (bad model id, invalid credential, malformed "
            "request, ...). Retrying this exact command will not help -- check "
            "--model-id/--model-provider (or the *_MODEL_ID/*_API_KEY env vars) and re-run."
        )

    def report(self) -> int:
        """Print `user_message()` and return the exit code a CLI should use."""

        print(f"\n[{'RETRYABLE' if self.retryable else 'FATAL'}] {self}")
        print(self.user_message())
        return 1


def report_model_provider_error(error: Exception) -> int:
    """One place for "what do we print, what exit code" for any model failure.

    `ModelProviderError` is the only exception `ModelProviderPort.complete()`
    implementations in this codebase raise for a failed call, so callers only
    need to catch that one type. Anything else is not a model-provider
    failure and is re-raised so it still surfaces as a real traceback instead
    of being misreported.
    """

    if isinstance(error, ModelProviderError):
        return error.report()
    raise error
