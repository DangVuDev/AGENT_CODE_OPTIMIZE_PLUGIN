"""Shared call logic for OpenAI Chat Completions-API-compatible adapters.

Not a public adapter itself — `OpenAIModelProvider`, `DeepSeekModelProvider`
and `OllamaModelProvider` are deliberately separate public classes (each
vendor's auth/base-url story differs enough to warrant its own type), but
all three send the exact same request shape once a client exists, so that
one piece is factored here instead of copy-pasted three times.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelCompletionResult,
    ModelRole,
)

if TYPE_CHECKING:
    from openai import OpenAI

_TOOL_NAME = "emit_result"
_JUDGE_TEMPERATURE = 0.0
_GENERATOR_TEMPERATURE = 0.2


def complete_via_openai_chat(
    client: OpenAI, request: ModelCompletionRequest
) -> ModelCompletionResult:
    is_judge = request.role is ModelRole.JUDGE
    temperature = _JUDGE_TEMPERATURE if is_judge else _GENERATOR_TEMPERATURE

    messages: list[dict[str, str]] = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]

    response = client.chat.completions.create(
        model=request.model_id,
        max_tokens=request.max_output_tokens,
        temperature=temperature,
        messages=messages,  # type: ignore[arg-type]
        tools=[
            {
                "type": "function",
                "function": {
                    "name": _TOOL_NAME,
                    "description": "Return the structured result for this request.",
                    "parameters": request.response_schema,
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
    )

    choice = response.choices[0]
    parsed_json: dict[str, Any] | None = None
    tool_calls = choice.message.tool_calls or []
    for call in tool_calls:
        if call.type == "function" and call.function.name == _TOOL_NAME:
            try:
                parsed_json = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                parsed_json = None
            break

    usage = response.usage

    return ModelCompletionResult(
        request_id=response.id,
        model_id=response.model,
        model_version=response.model,
        raw_text=choice.message.content or (str(parsed_json) if parsed_json is not None else ""),
        parsed_json=parsed_json,
        valid_json=parsed_json is not None,
        input_tokens=usage.prompt_tokens if usage is not None else 0,
        output_tokens=usage.completion_tokens if usage is not None else 0,
        stop_reason=choice.finish_reason or "unknown",
    )


__all__ = ["complete_via_openai_chat"]
