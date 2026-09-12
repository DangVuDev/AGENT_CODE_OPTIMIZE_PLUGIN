from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

import pytest

from production_optimizer.adapters.production.anthropic_model_provider import AnthropicModelProvider
from production_optimizer.contracts.platform import ModelCompletionRequest, ModelMessage, ModelRole


def _require_api_key() -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY is not set; skipping real Anthropic API smoke test")
    try:
        import anthropic
    except ImportError:
        pytest.skip("anthropic package is not installed; run `uv sync --all-groups`")
    del anthropic
    return api_key


class _EnvSecretsBroker:
    """Leases a secret straight from the environment for this one smoke test.

    A real production `SecretsBroker` (Vault, AWS Secrets Manager, ...)
    would resolve `secret_ref` against a real secret store; this test only
    needs to prove `AnthropicModelProvider` genuinely goes through the
    `SecretsBroker.lease` contract (ADR-0002) rather than reading
    `ANTHROPIC_API_KEY` itself.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @contextmanager
    def lease(
        self, *, tenant_id: str, secret_ref: str, purpose: str, ttl_seconds: int
    ) -> Generator[str]:
        del tenant_id, secret_ref, purpose, ttl_seconds
        yield self._api_key


def test_anthropic_model_provider_completes_a_real_request() -> None:
    """One real, billed API call — never part of the default `pytest -q` run.

    Skips outright unless `ANTHROPIC_API_KEY` is set (and the `anthropic`
    package is installed), mirroring `_require_database`'s skip-if-
    unreachable pattern for Postgres/S3 elsewhere in this directory.
    """

    api_key = _require_api_key()
    provider = AnthropicModelProvider(
        secrets=_EnvSecretsBroker(api_key), tenant_id="TENANT-SMOKE", secret_ref="anthropic-api-key"
    )

    request = ModelCompletionRequest(
        role=ModelRole.GENERATOR,
        model_id="claude-haiku-4-5-20251001",
        prompt_version="smoke-v1",
        messages=[
            ModelMessage(
                role="user", content="Reply using the emit_result tool with color set to 'blue'."
            )
        ],
        response_schema={
            "type": "object",
            "properties": {"color": {"type": "string"}},
            "required": ["color"],
        },
        max_output_tokens=100,
        idempotency_key="smoke-test-1",
    )

    result = provider.complete(request)

    assert result.valid_json is True
    assert result.parsed_json is not None
    assert "color" in result.parsed_json
    assert result.input_tokens > 0
    assert result.output_tokens > 0
