from __future__ import annotations

import json
from typing import Any, Literal

from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelCompletionResult,
    ModelRole,
)
from production_optimizer.ports.secrets import SecretsBroker

from ._openai_compatible import complete_via_openai_chat
from .model_provider_errors import ModelProviderError

ModelProviderKind = Literal["anthropic", "openai", "gemini", "deepseek", "ollama"]

_LOCAL_PLACEHOLDER_KEY = "ollama"
_TOOL_NAME = "emit_result"
_JUDGE_TEMPERATURE = 0.0
_GENERATOR_TEMPERATURE = 0.2


class GenericModelProvider:
    """One configurable `ModelProviderPort` for every approved model backend.

    Business nodes should depend only on `ModelProviderPort`; they should not
    know whether the runtime is OpenAI, Anthropic, Gemini, DeepSeek, Ollama, or
    another OpenAI-compatible endpoint. This adapter is the single production
    entry point for provider-specific wire protocols. `complete()` catches
    every SDK-specific exception and re-raises the one `ModelProviderError`
    type (see `model_provider_errors.py`) so no caller needs to know which
    provider it is talking to, or which of five different SDKs' exception
    hierarchies a failure came from, to handle it. There is deliberately no
    retry/backoff/fallback here: a failed call fails once, immediately, with
    a clear reason -- deciding whether to try again belongs to whatever
    calls `complete()`, not to this adapter.
    """

    def __init__(
        self,
        *,
        provider: ModelProviderKind,
        secrets: SecretsBroker | None = None,
        tenant_id: str | None = None,
        secret_ref: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if provider != "ollama" and secrets is None:
            raise ValueError(f"{provider} requires a SecretsBroker")
        if secrets is not None and (tenant_id is None or secret_ref is None):
            raise ValueError("tenant_id and secret_ref are required when secrets are configured")
        self.provider = provider
        self._secrets = secrets
        self._tenant_id = tenant_id
        self._secret_ref = secret_ref
        self._base_url = base_url

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        try:
            if self.provider in {"openai", "deepseek", "ollama"}:
                return self._complete_openai_compatible(request)
            if self.provider == "anthropic":
                return self._complete_anthropic(request)
            if self.provider == "gemini":
                return self._complete_gemini(request)
            raise AssertionError(f"unsupported provider: {self.provider}")
        except ModelProviderError:
            raise
        except Exception as error:
            raise ModelProviderError(
                provider_name=self.provider,
                model_id=request.model_id,
                cause=error,
            ) from error

    def healthcheck(self) -> bool:
        try:
            if self.provider in {"openai", "deepseek", "ollama"}:
                import openai

                del openai
            elif self.provider == "anthropic":
                import anthropic

                del anthropic
            elif self.provider == "gemini":
                from google import genai

                del genai
        except ImportError:
            return False
        return True

    def _leased_key(self, *, request: ModelCompletionRequest) -> Any:
        if self._secrets is None:
            return None
        assert self._tenant_id is not None
        assert self._secret_ref is not None
        return self._secrets.lease(
            tenant_id=self._tenant_id,
            secret_ref=self._secret_ref,
            purpose=f"model-completion:{self.provider}:{request.role.value}",
            ttl_seconds=60,
        )

    def _complete_openai_compatible(
        self, request: ModelCompletionRequest
    ) -> ModelCompletionResult:
        import openai

        lease = self._leased_key(request=request)
        if lease is None:
            client = (
                openai.OpenAI(api_key=_LOCAL_PLACEHOLDER_KEY, base_url=self._base_url)
                if self._base_url
                else openai.OpenAI(api_key=_LOCAL_PLACEHOLDER_KEY)
            )
            return complete_via_openai_chat(client, request)

        with lease as api_key:
            client = (
                openai.OpenAI(api_key=api_key, base_url=self._base_url)
                if self._base_url
                else openai.OpenAI(api_key=api_key)
            )
            return complete_via_openai_chat(client, request)

    def _complete_anthropic(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        import anthropic

        system_prompt = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        turn_messages: list[anthropic.types.MessageParam] = [
            {"role": message.role, "content": message.content}  # type: ignore[typeddict-item]
            for message in request.messages
            if message.role != "system"
        ]
        temperature = (
            _JUDGE_TEMPERATURE if request.role is ModelRole.JUDGE else _GENERATOR_TEMPERATURE
        )

        with self._leased_key(request=request) as api_key:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=request.model_id,
                max_tokens=request.max_output_tokens,
                temperature=temperature,
                system=system_prompt or anthropic.Omit(),
                messages=turn_messages,
                tools=[
                    {
                        "name": _TOOL_NAME,
                        "description": "Return the structured result for this request.",
                        "input_schema": request.response_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            )

        parsed_json: dict[str, Any] | None = None
        raw_text_parts: list[str] = []
        for block in response.content:
            if block.type == "tool_use" and block.name == _TOOL_NAME:
                parsed_json = dict(block.input)
            elif block.type == "text":
                raw_text_parts.append(block.text)

        return ModelCompletionResult(
            request_id=response.id,
            model_id=response.model,
            model_version=response.model,
            raw_text="\n".join(raw_text_parts) if raw_text_parts else str(parsed_json or ""),
            parsed_json=parsed_json,
            valid_json=parsed_json is not None,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason or "unknown",
        )

    def _complete_gemini(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        from google import genai
        from google.genai import types

        temperature = (
            _JUDGE_TEMPERATURE if request.role is ModelRole.JUDGE else _GENERATOR_TEMPERATURE
        )
        system_instruction = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        contents = [
            types.Content(
                role="model" if message.role == "assistant" else message.role,
                parts=[types.Part(text=message.content)],
            )
            for message in request.messages
            if message.role != "system"
        ]

        with self._leased_key(request=request) as api_key:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(  # pyright: ignore[reportUnknownMemberType]
                model=request.model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction or None,
                    temperature=temperature,
                    max_output_tokens=request.max_output_tokens,
                    response_mime_type="application/json",
                    response_json_schema=request.response_schema,
                ),
            )

        raw_text = response.text or ""
        parsed_json: dict[str, Any] | None = None
        if raw_text:
            try:
                parsed_json = json.loads(raw_text)
            except json.JSONDecodeError:
                parsed_json = None

        usage = response.usage_metadata
        candidates = response.candidates or []
        finish_reason = candidates[0].finish_reason if candidates else None

        return ModelCompletionResult(
            request_id=response.response_id or "unknown",
            model_id=response.model_version or request.model_id,
            model_version=response.model_version or "unknown",
            raw_text=raw_text,
            parsed_json=parsed_json,
            valid_json=parsed_json is not None,
            input_tokens=(usage.prompt_token_count or 0) if usage is not None else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage is not None else 0,
            stop_reason=str(finish_reason) if finish_reason is not None else "unknown",
        )
