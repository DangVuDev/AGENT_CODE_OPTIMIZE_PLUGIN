from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.platform import ModelCompletionRequest, ModelCompletionResult


class ModelProviderPort(Protocol):
    """Provider-agnostic access to a generative model call.

    No Anthropic/OpenAI-specific type crosses this boundary — `complete`
    takes and returns only `contracts.platform` types. `ModelCompletionRequest.
    role` (`ModelRole.GENERATOR`/`ModelRole.JUDGE`) is what makes "the
    generator cannot judge itself" (A3 playbook) mechanically enforceable: a
    judge call is a structurally separate request with its own message
    list, never a continuation of a generator's conversation. One-bounded-
    repair-on-invalid-JSON and token-budget enforcement are the calling
    node's responsibility, not this port's — it only reports what happened
    (`valid_json`, `input_tokens`/`output_tokens`) for the caller to act on.
    """

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult: ...

    def healthcheck(self) -> bool: ...
