from __future__ import annotations

import os
import socket
from collections.abc import Generator
from contextlib import contextmanager
from urllib.parse import urlsplit

import pytest

from production_optimizer.adapters.production.deepseek_model_provider import DeepSeekModelProvider
from production_optimizer.adapters.production.ollama_model_provider import OllamaModelProvider
from production_optimizer.adapters.production.openai_model_provider import OpenAIModelProvider
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"color": {"type": "string"}},
    "required": ["color"],
}


def _require_openai_sdk() -> None:
    try:
        import openai
    except ImportError:
        pytest.skip("openai package is not installed; run `uv sync --all-groups`")
    del openai


def _require_env_key(var_name: str) -> str:
    api_key = os.environ.get(var_name)
    if not api_key:
        pytest.skip(f"{var_name} is not set; skipping real API smoke test")
    _require_openai_sdk()
    return api_key


class _EnvSecretsBroker:
    """Leases a secret straight from the environment for this one smoke test.

    Mirrors `test_anthropic_model_provider.py`'s fake — proves each adapter
    genuinely goes through `SecretsBroker.lease` (ADR-0002) rather than
    reading the API key from the environment itself.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @contextmanager
    def lease(
        self, *, tenant_id: str, secret_ref: str, purpose: str, ttl_seconds: int
    ) -> Generator[str]:
        del tenant_id, secret_ref, purpose, ttl_seconds
        yield self._api_key


def _request(*, model_id: str, idempotency_key: str) -> ModelCompletionRequest:
    return ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id=model_id,
        prompt_version="smoke-v1",
        messages=[
            ModelMessage(
                role="user", content="Reply using the emit_result tool with color set to 'blue'."
            )
        ],
        response_schema=_RESPONSE_SCHEMA,
        max_output_tokens=100,
        idempotency_key=idempotency_key,
    )


def test_openai_model_provider_completes_a_real_request() -> None:
    """One real, billed OpenAI API call — never part of the default `pytest -q` run."""

    api_key = _require_env_key("OPENAI_API_KEY")
    provider = OpenAIModelProvider(
        secrets=_EnvSecretsBroker(api_key), tenant_id="TENANT-SMOKE", secret_ref="openai-api-key"
    )

    result = provider.complete(_request(model_id="gpt-4o-mini", idempotency_key="smoke-openai-1"))

    assert result.valid_json is True
    assert result.parsed_json is not None
    assert "color" in result.parsed_json
    assert result.input_tokens > 0
    assert result.output_tokens > 0


def test_deepseek_model_provider_completes_a_real_request() -> None:
    """One real, billed DeepSeek API call — never part of the default `pytest -q` run."""

    api_key = _require_env_key("DEEPSEEK_API_KEY")
    provider = DeepSeekModelProvider(
        secrets=_EnvSecretsBroker(api_key), tenant_id="TENANT-SMOKE", secret_ref="deepseek-api-key"
    )

    result = provider.complete(
        _request(model_id="deepseek-chat", idempotency_key="smoke-deepseek-1")
    )

    assert result.valid_json is True
    assert result.parsed_json is not None
    assert "color" in result.parsed_json
    assert result.input_tokens > 0
    assert result.output_tokens > 0


def _require_ollama(base_url: str) -> None:
    _require_openai_sdk()
    parsed = urlsplit(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=1.0):
            pass
    except OSError:
        pytest.skip(
            f"no local Ollama-compatible server reachable at {host}:{port}; "
            "start one (e.g. `ollama serve`) to run this test"
        )


def test_ollama_model_provider_completes_a_real_request() -> None:
    """One real call to a local Ollama (or compatible) server.

    Free (no API cost) unlike the hosted adapters' smoke tests, but still
    excluded from the default `pytest -q` run because it needs a real local
    server — skips if nothing is listening on the configured port.
    """

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    _require_ollama(base_url)
    model_id = os.environ.get("OLLAMA_MODEL_ID", "llama3.2")
    provider = OllamaModelProvider(base_url=base_url)

    result = provider.complete(_request(model_id=model_id, idempotency_key="smoke-ollama-1"))

    assert result.valid_json is True
    assert result.parsed_json is not None
    assert "color" in result.parsed_json
