from __future__ import annotations

from typing import Any

from production_optimizer.contracts.platform import (
    ModelCompletionRequest,
    ModelCompletionResult,
    ModelRole,
)
from production_optimizer.ports.secrets import SecretsBroker

_TOOL_NAME = "emit_result"
_JUDGE_TEMPERATURE = 0.0
_GENERATOR_TEMPERATURE = 0.2


class AnthropicModelProvider:
    """`ModelProviderPort` backed by the Anthropic Messages API.

    Forces a structured reply via Anthropic's tool-use mechanism: the
    caller's `response_schema` becomes the one tool's `input_schema`, and
    `tool_choice` pins the model to call exactly that tool, so
    `parsed_json`/`valid_json` reflect a real schema-conformant response
    rather than best-effort text parsing.

    The API key is leased per call via `SecretsBroker` (never read from the
    environment or cached on the instance) — see
    `docs/adr/0002-model-provider-port.md`. Judge calls use temperature 0;
    generator calls use a low but nonzero temperature, matching the
    playbook's expectation that the judge, not the generator, is the
    deterministic-leaning check.
    """

    def __init__(self, *, secrets: SecretsBroker, tenant_id: str, secret_ref: str) -> None:
        self._secrets = secrets
        self._tenant_id = tenant_id
        self._secret_ref = secret_ref

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        import anthropic

        system_prompt = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        turn_messages: list[anthropic.types.MessageParam] = [
            {"role": message.role, "content": message.content}  # type: ignore[typeddict-item]
            for message in request.messages
            if message.role != "system"
        ]
        is_judge = request.role is ModelRole.JUDGE
        temperature = _JUDGE_TEMPERATURE if is_judge else _GENERATOR_TEMPERATURE

        with self._secrets.lease(
            tenant_id=self._tenant_id,
            secret_ref=self._secret_ref,
            purpose=f"a3-model-completion:{request.role.value}",
            ttl_seconds=60,
        ) as api_key:
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

    def healthcheck(self) -> bool:
        try:
            import anthropic
        except ImportError:
            return False
        del anthropic
        return True
