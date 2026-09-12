from __future__ import annotations

from production_optimizer.contracts.platform import ModelCompletionRequest, ModelCompletionResult
from production_optimizer.ports.secrets import SecretsBroker

from ._openai_compatible import complete_via_openai_chat

_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_LOCAL_PLACEHOLDER_KEY = "ollama"  # Ollama's own documented placeholder; no real secret exists.


class OllamaModelProvider:
    """`ModelProviderPort` backed by a local Ollama server (or any other
    self-hosted OpenAI-compatible runtime — vLLM, LM Studio, llama.cpp
    server's `--api` mode — listening at the same base URL shape).

    Unlike the hosted adapters, `secrets` is optional: a local server
    typically has no real credential to lease, so this defaults to Ollama's
    own documented placeholder API key rather than requiring a
    `SecretsBroker` a purely local deployment may not have configured. If a
    `secrets`/`secret_ref` pair *is* given (e.g. an authenticating proxy in
    front of a shared local server), it is leased exactly like the hosted
    adapters — never read from the environment.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        secrets: SecretsBroker | None = None,
        tenant_id: str | None = None,
        secret_ref: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._secrets = secrets
        self._tenant_id = tenant_id
        self._secret_ref = secret_ref

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        import openai

        if (
            self._secrets is not None
            and self._tenant_id is not None
            and self._secret_ref is not None
        ):
            with self._secrets.lease(
                tenant_id=self._tenant_id,
                secret_ref=self._secret_ref,
                purpose=f"a3-model-completion:{request.role.value}",
                ttl_seconds=60,
            ) as api_key:
                client = openai.OpenAI(api_key=api_key, base_url=self._base_url)
                return complete_via_openai_chat(client, request)

        client = openai.OpenAI(api_key=_LOCAL_PLACEHOLDER_KEY, base_url=self._base_url)
        return complete_via_openai_chat(client, request)

    def healthcheck(self) -> bool:
        try:
            import openai
        except ImportError:
            return False
        del openai
        return True
