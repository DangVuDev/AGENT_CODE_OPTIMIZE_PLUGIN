"""Shared in-memory adapters and model-provider selection for the Lane 1
demo/CLI scripts (`demo_lane1_a1_a2_a3.py`, `optimize.py`).

Not a package module under `src/` on purpose: these are throwaway,
in-process stand-ins for `ArtifactStore`/`IntentLedger` meant for a one-off
script run against a local repository, not for anything checkpointed or
shared across processes -- production code uses the real adapters in
`adapters/production/`.
"""

from __future__ import annotations

import os
import re
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import sha256_digest
from production_optimizer.contracts.platform import (
    IntentRecord,
    IntentStatus,
    ModelCompletionRequest,
    ModelCompletionResult,
    PolicyDecision,
    PolicyRequest,
)


def _load_dotenv() -> None:
    """Populate `os.environ` from a `.env` file at the repo root, if present.

    A real shell-exported variable always wins (`setdefault`, never
    overwrite) -- this only fills in what the shell didn't already set.
    Deliberately minimal (no quoting/escaping/multiline support) rather than
    a `python-dotenv` dependency: these two CLI scripts are the only
    consumers, and `.env.example`'s `KEY=value` lines never need more than
    this.
    """

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


class MemoryArtifactStore:
    def __init__(self) -> None:
        self._content_by_uri: dict[str, bytes] = {}

    def put_json(
        self, *, tenant_id: str, content: bytes, content_digest: str, idempotency_key: str
    ) -> ArtifactRef:
        del tenant_id
        uri = f"memory://{idempotency_key}"
        self._content_by_uri[uri] = content
        return ArtifactRef(
            artifact_type="JsonArtifact",
            schema_version="1.0",
            artifact_id=idempotency_key,
            content_digest=content_digest,
            uri=uri,
        )

    def put_blob(
        self, *, tenant_id: str, content: bytes, content_digest: str, media_type: str
    ) -> ArtifactRef:
        del tenant_id, media_type
        uri = f"memory://blob/{content_digest}"
        self._content_by_uri[uri] = content
        return ArtifactRef(
            artifact_type="Blob",
            schema_version="1.0",
            artifact_id=content_digest,
            content_digest=content_digest,
            uri=uri,
        )

    def read(self, *, tenant_id: str, ref: ArtifactRef) -> bytes:
        del tenant_id
        return self._content_by_uri[ref.uri]

    def verify(self, *, tenant_id: str, ref: ArtifactRef) -> bool:
        del tenant_id
        content = self._content_by_uri.get(ref.uri)
        return content is not None and sha256_digest(content) == ref.content_digest

    def seed_json(self, ref: ArtifactRef, content: bytes) -> None:
        self._content_by_uri[ref.uri] = content


class MemoryIntentLedger:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IntentRecord] = {}

    def prepare(self, intent: IntentRecord) -> IntentRecord:
        key = (intent.tenant_id, intent.idempotency_key)
        existing = self._records.get(key)
        if existing is not None and existing.status is IntentStatus.COMPLETED:
            return existing
        self._records[key] = intent
        return intent

    def get(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord | None:
        return self._records.get((tenant_id, idempotency_key))

    def complete(
        self, *, tenant_id: str, idempotency_key: str, output_ref: ArtifactRef
    ) -> IntentRecord:
        record = self._records[(tenant_id, idempotency_key)]
        updated = record.model_copy(
            update={"status": IntentStatus.COMPLETED, "output_ref": output_ref}
        )
        self._records[(tenant_id, idempotency_key)] = updated
        return updated

    def mark_unknown(self, *, tenant_id: str, idempotency_key: str) -> IntentRecord:
        record = self._records[(tenant_id, idempotency_key)]
        updated = record.model_copy(update={"status": IntentStatus.UNKNOWN})
        self._records[(tenant_id, idempotency_key)] = updated
        return updated


class AllowPolicy:
    """Fixed allow-everything `PolicyPort` for a one-off local script run.

    Production code uses `DeterministicPythonPolicy`
    (`adapters/production/python_policy.py`), which is fail-closed by
    default and requires registering real decision functions.
    """

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=True, decision="allow", policy_version=request.policy_version, reasons=[]
        )

    def healthcheck(self) -> bool:
        return True


@contextmanager
def _env_secret_lease(api_key: str) -> Any:
    yield api_key


class EnvSecretsBroker:
    """Leases a secret straight from the environment for a one-off script run.

    A real `SecretsBroker` (Vault, AWS Secrets Manager, ...) would resolve
    `secret_ref` against a real secret store.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def lease(self, *, tenant_id: str, secret_ref: str, purpose: str, ttl_seconds: int) -> Any:
        del tenant_id, secret_ref, purpose, ttl_seconds
        return _env_secret_lease(self._api_key)


_EVIDENCE_ID_LINE = re.compile(r"Available evidence IDs \(cite only these\): (.+)")
_FINDING_LINE = re.compile(r"^- (finding-\S+)")
_SIGNAL_LINE = re.compile(r"^- (signal-\S+)")


class LocalScriptedModelProvider:
    """A deterministic, zero-cost stand-in for a real `ModelProviderPort`.

    NOT a real model -- used only when no real LLM credentials are found so
    these scripts still run end-to-end. It extracts real evidence/finding/
    signal IDs from the context it is given (never invents its own), so
    A3.41's citation resolution and A3.51's finding assembly still operate
    on genuine references, same as they would against a real model's reply.
    """

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult:
        self.calls += 1
        context = "\n".join(message.content for message in request.messages)
        payload: dict[str, Any] | None
        if ":A3.40:" in request.idempotency_key:
            payload = self._finding_payload(context)
        elif ":A3.50:" in request.idempotency_key:
            payload = self._judge_payload(context)
        elif ":A3.60:" in request.idempotency_key:
            payload = self._strategy_payload(context)
        else:
            payload = None

        return ModelCompletionResult(
            request_id=f"local-{self.calls}",
            model_id=request.model_id,
            model_version="local-scripted-1",
            raw_text=str(payload) if payload is not None else "",
            parsed_json=payload,
            valid_json=payload is not None,
            input_tokens=0,
            output_tokens=0,
            stop_reason="end_turn" if payload is not None else "refusal",
        )

    def healthcheck(self) -> bool:
        return True

    def _finding_payload(self, context: str) -> dict[str, Any]:
        match = _EVIDENCE_ID_LINE.search(context)
        evidence_ids = [
            e.strip() for e in (match.group(1).split(",") if match else []) if e.strip()
        ]
        unit_evidence = [e for e in evidence_ids if ":A2.61:" in e]
        chosen = unit_evidence[:1] or evidence_ids[:1] or ["unknown-evidence"]
        signal_ids = _SIGNAL_LINE.findall(context) or ["signal-unknown"]
        return {
            "findings": [
                {
                    "finding_id": "finding-guardrail-violation",
                    "problem_signal_ids": signal_ids[:1],
                    "claim_type": "hypothesis",
                    "symptom": "a repository-owned command guardrail is violated",
                    "causal_claim": (
                        "the failing/violating command's evidence directly explains the "
                        "guardrail breach"
                    ),
                    "supporting_evidence_ids": chosen,
                    "confidence": 0.6,
                    "unknowns": ["exact root cause line was not inspected by this local stand-in"],
                }
            ]
        }

    def _judge_payload(self, context: str) -> dict[str, Any]:
        finding_id_match = re.search(r"Finding (\S+) \(", context)
        finding_id = finding_id_match.group(1) if finding_id_match else "finding-unknown"
        accept = "all_resolved=True" in context
        return {
            "finding_id": finding_id,
            "verdict": "accept" if accept else "reject",
            "reasons": ["citations fully resolved"] if accept else ["citations not fully resolved"],
        }

    def _strategy_payload(self, context: str) -> dict[str, Any]:
        finding_ids = _FINDING_LINE.findall(context) or ["finding-guardrail-violation"]
        evidence_match = re.search(r"evidence=\[(.*?)\]", context)
        evidence_ids = (
            [e.strip().strip("'\"") for e in evidence_match.group(1).split(",") if e.strip()]
            if evidence_match
            else []
        ) or ["fallback-evidence-id"]
        return {
            "strategies": [
                {
                    "strategy_id": "strategy-local-1",
                    "finding_ids": finding_ids,
                    "title": "Address the detected guardrail violation",
                    "mechanism": (
                        "Investigate the cited evidence and correct the underlying code or test "
                        "so the guardrail's repository-owned command passes again."
                    ),
                    "strategy_tradeoffs": "Directly addresses the guardrail violation detected.",
                    "phase_templates": [
                        {
                            "phase_id": "phase-1",
                            "sequence": 1,
                            "phase_kind": "diagnostic",
                            "treatment": {
                                "variable": "root cause",
                                "before": "unknown",
                                "after": "identified from cited evidence",
                            },
                        },
                        {
                            "phase_id": "phase-2",
                            "sequence": 2,
                            "phase_kind": "implementation",
                            "treatment": {
                                "variable": "guardrail command result",
                                "before": "failing",
                                "after": "passing",
                            },
                        },
                    ],
                    "risk_ceiling": "code",
                    "evidence_ids": evidence_ids,
                    "assumptions": [],
                    "target_paths": [],
                }
            ]
        }


def select_model_provider(*, tenant_id: str) -> tuple[Any, str]:
    """Pick a real `ModelProviderPort` if credentials/a local server exist,
    else fall back to `LocalScriptedModelProvider` (clearly not real).

    Returns `(provider, model_id)` -- the model name is provider-specific
    (an Anthropic alias, a local Ollama tag, ...) and must travel with the
    provider it was chosen for, never a single constant reused across all of
    them (see `NodePorts.model_id` and `a3_handlers._model_id`).
    """

    if api_key := os.environ.get("ANTHROPIC_API_KEY"):
        from production_optimizer.adapters.production import AnthropicModelProvider

        model_id = os.environ.get("ANTHROPIC_MODEL_ID", "claude-sonnet-5")
        print(f"[model] using AnthropicModelProvider (ANTHROPIC_API_KEY found), model={model_id}")
        return (
            AnthropicModelProvider(
                secrets=EnvSecretsBroker(api_key),
                tenant_id=tenant_id,
                secret_ref="anthropic-api-key",
            ),
            model_id,
        )
    if api_key := os.environ.get("OPENAI_API_KEY"):
        from production_optimizer.adapters.production import OpenAIModelProvider

        model_id = os.environ.get("OPENAI_MODEL_ID", "gpt-4o-mini")
        print(f"[model] using OpenAIModelProvider (OPENAI_API_KEY found), model={model_id}")
        return (
            OpenAIModelProvider(
                secrets=EnvSecretsBroker(api_key), tenant_id=tenant_id, secret_ref="openai-api-key"
            ),
            model_id,
        )
    if api_key := os.environ.get("GEMINI_API_KEY"):
        from production_optimizer.adapters.production import GeminiModelProvider

        model_id = os.environ.get("GEMINI_MODEL_ID", "gemini-2.5-flash")
        print(f"[model] using GeminiModelProvider (GEMINI_API_KEY found), model={model_id}")
        return (
            GeminiModelProvider(
                secrets=EnvSecretsBroker(api_key), tenant_id=tenant_id, secret_ref="gemini-api-key"
            ),
            model_id,
        )
    if api_key := os.environ.get("DEEPSEEK_API_KEY"):
        from production_optimizer.adapters.production import DeepSeekModelProvider

        model_id = os.environ.get("DEEPSEEK_MODEL_ID", "deepseek-chat")
        print(f"[model] using DeepSeekModelProvider (DEEPSEEK_API_KEY found), model={model_id}")
        return (
            DeepSeekModelProvider(
                secrets=EnvSecretsBroker(api_key),
                tenant_id=tenant_id,
                secret_ref="deepseek-api-key",
            ),
            model_id,
        )

    ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    parsed = urlsplit(ollama_base_url)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or 11434), timeout=0.5
        ):
            from production_optimizer.adapters.production import OllamaModelProvider

            model_id = os.environ.get("OLLAMA_MODEL_ID", "llama3.2")
            print(
                f"[model] using OllamaModelProvider (server reachable at {ollama_base_url}), "
                f"model={model_id}"
            )
            return OllamaModelProvider(base_url=ollama_base_url), model_id
    except OSError:
        pass

    print(
        "[model] no ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY/DEEPSEEK_API_KEY set and no "
        "local Ollama server reachable -- using LocalScriptedModelProvider (NOT a real model). "
        "Set one of those env vars (see .env.example) to see real LLM output."
    )
    return LocalScriptedModelProvider(), "local-scripted"


def read_model(
    store: MemoryArtifactStore, tenant_id: str, ref: ArtifactRef, model: type[Any]
) -> Any:
    content = store.read(tenant_id=tenant_id, ref=ref)
    data = TypeAdapter(dict[str, Any]).validate_json(content)
    data.setdefault("content_digest", ref.content_digest)
    return model.model_validate(data)


def ref_by_type(state: dict[str, Any], artifact_type: str) -> ArtifactRef | None:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    return None
