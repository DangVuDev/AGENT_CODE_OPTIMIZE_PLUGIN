from __future__ import annotations

from production_optimizer.contracts.platform import ModelCompletionRequest, ModelCompletionResult
from production_optimizer.ports.secrets import SecretsBroker

from ._openai_compatible import complete_via_openai_chat


class OpenAIModelProvider:
    """`ModelProviderPort` backed by the OpenAI Chat Completions API.

    Forces a structured reply via OpenAI's function-calling mechanism: the
    caller's `response_schema` becomes the one tool's `parameters`, and
    `tool_choice` pins the model to call exactly that tool — mirrors
    `AnthropicModelProvider`'s structured-output approach so both adapters
    give identical `valid_json`/`parsed_json` semantics to a calling node.

    The API key is leased per call via `SecretsBroker` (never read from the
    environment or cached on the instance) — see
    `docs/adr/0002-model-provider-port.md`.
    """

    def __init__(self, *, secrets: SecretsBroker, tenant_id: str, secret_ref: str) -> None:
        self._secrets = secrets
        self._tenant_id = tenant_id
        self._secret_ref = secret_ref

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        import openai

        with self._secrets.lease(
            tenant_id=self._tenant_id,
            secret_ref=self._secret_ref,
            purpose=f"a3-model-completion:{request.role.value}",
            ttl_seconds=60,
        ) as api_key:
            client = openai.OpenAI(api_key=api_key)
            return complete_via_openai_chat(client, request)

    def healthcheck(self) -> bool:
        try:
            import openai
        except ImportError:
            return False
        del openai
        return True
