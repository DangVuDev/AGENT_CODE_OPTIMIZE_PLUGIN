from __future__ import annotations

import json
from typing import Any

from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelCompletionResult,
    ModelRole,
)
from production_optimizer.ports.secrets import SecretsBroker

_JUDGE_TEMPERATURE = 0.0
_GENERATOR_TEMPERATURE = 0.2


class GeminiModelProvider:
    """`ModelProviderPort` backed by Google's Gemini API (`google-genai` SDK).

    Uses Gemini's native JSON-schema-constrained output
    (`response_mime_type="application/json"` + `response_json_schema`)
    rather than the tool-calling pattern the other adapters use — Gemini
    supports this directly, and it needs no synthetic "emit_result" tool
    wrapper to get a schema-conformant reply.

    The API key is leased per call via `SecretsBroker` (never read from the
    environment or cached on the instance) — see
    `docs/adr/0002-model-provider-port.md`.
    """

    def __init__(self, *, secrets: SecretsBroker, tenant_id: str, secret_ref: str) -> None:
        self._secrets = secrets
        self._tenant_id = tenant_id
        self._secret_ref = secret_ref

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        from google import genai
        from google.genai import types

        is_judge = request.role is ModelRole.JUDGE
        temperature = _JUDGE_TEMPERATURE if is_judge else _GENERATOR_TEMPERATURE

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

        with self._secrets.lease(
            tenant_id=self._tenant_id,
            secret_ref=self._secret_ref,
            purpose=f"a3-model-completion:{request.role.value}",
            ttl_seconds=60,
        ) as api_key:
            client = genai.Client(api_key=api_key)
            # google-genai's `contents` parameter type is a large Union whose
            # overload resolution pyright cannot fully narrow in strict mode
            # (unrelated to this call's actual arguments) — same class of
            # known SDK-typing gap as boto3/psycopg (see CLAUDE.md
            # troubleshooting).
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

    def healthcheck(self) -> bool:
        try:
            from google import genai
        except ImportError:
            return False
        del genai
        return True
