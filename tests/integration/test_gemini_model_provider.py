from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

import pytest

from production_optimizer.adapters.production.gemini_model_provider import GeminiModelProvider
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole


def _require_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY is not set; skipping real Gemini API smoke test")
    try:
        from google import genai
    except ImportError:
        pytest.skip("google-genai package is not installed; run `uv sync --all-groups`")
    del genai
    return api_key


class _EnvSecretsBroker:
    """Leases a secret straight from the environment for this one smoke test.

    Mirrors `test_anthropic_model_provider.py`'s fake — proves
    `GeminiModelProvider` genuinely goes through `SecretsBroker.lease`
    (ADR-0002) rather than reading `GEMINI_API_KEY` itself.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @contextmanager
    def lease(
        self, *, tenant_id: str, secret_ref: str, purpose: str, ttl_seconds: int
    ) -> Generator[str]:
        del tenant_id, secret_ref, purpose, ttl_seconds
        yield self._api_key


def test_gemini_model_provider_completes_a_real_request() -> None:
    """One real, billed Gemini API call — never part of the default `pytest -q` run."""

    api_key = _require_api_key()
    provider = GeminiModelProvider(
        secrets=_EnvSecretsBroker(api_key), tenant_id="TENANT-SMOKE", secret_ref="gemini-api-key"
    )

    request = ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id="gemini-2.5-flash",
        prompt_version="smoke-v1",
        messages=[ModelMessage(role="user", content="Respond with color set to 'blue'.")],
        response_schema={
            "type": "object",
            "properties": {"color": {"type": "string"}},
            "required": ["color"],
        },
        max_output_tokens=100,
        idempotency_key="smoke-gemini-1",
    )

    result = provider.complete(request)

    assert result.valid_json is True
    assert result.parsed_json is not None
    assert "color" in result.parsed_json
    assert result.input_tokens > 0
    assert result.output_tokens > 0
