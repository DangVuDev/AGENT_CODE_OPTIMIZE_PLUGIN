from __future__ import annotations

from production_optimizer.contracts.platform import ModelCompletionRequest, ModelCompletionResult
from production_optimizer.ports.secrets import SecretsBroker

from ._openai_compatible import complete_via_openai_chat

_DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekModelProvider:
    """`ModelProviderPort` backed by DeepSeek's OpenAI-compatible API.

    DeepSeek's chat API is wire-compatible with OpenAI's Chat Completions
    endpoint, so this uses the same `openai` SDK as `OpenAIModelProvider`,
    just pointed at a different `base_url` — a separate class (not a
    parameterized shared one) because the two vendors' credentials and
    default configuration are managed independently.

    The API key is leased per call via `SecretsBroker` — see
    `docs/adr/0002-model-provider-port.md`.
    """

    def __init__(
        self,
        *,
        secrets: SecretsBroker,
        tenant_id: str,
        secret_ref: str,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._secrets = secrets
        self._tenant_id = tenant_id
        self._secret_ref = secret_ref
        self._base_url = base_url

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        import openai

        with self._secrets.lease(
            tenant_id=self._tenant_id,
            secret_ref=self._secret_ref,
            purpose=f"a3-model-completion:{request.role.value}",
            ttl_seconds=60,
        ) as api_key:
            client = openai.OpenAI(api_key=api_key, base_url=self._base_url)
            return complete_via_openai_chat(client, request)

    def healthcheck(self) -> bool:
        try:
            import openai
        except ImportError:
            return False
        del openai
        return True
