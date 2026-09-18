"""Shared in-memory adapters and model-provider selection for the Lane 1
demo/CLI scripts (`demo_lane1_a1_a2_a3.py`, `optimize.py`).

Not a package module under `src/` on purpose: these are throwaway,
in-process stand-ins for `ArtifactStore`/`IntentLedger` meant for a one-off
script run against a local repository, not for anything checkpointed or
shared across processes -- production code uses the real adapters in
`adapters/production/`.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
from contextlib import contextmanager
from dataclasses import dataclass
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
        # `artifact_id` here is a generic, store-internal placeholder --
        # every real caller (`_put_envelope` in each `*_handlers` module)
        # discards it and uses `envelope.artifact_id` instead (only `.uri`
        # from this return value is actually used). `idempotency_key` itself
        # can exceed `ArtifactRef.artifact_id`'s 100-char limit once it
        # embeds a already-long, pass-scoped `envelope.artifact_id` a second
        # time (case_id:node_id:artifact_type:envelope.artifact_id) -- using
        # a fixed-length digest instead of the raw key avoids that failure
        # without affecting uniqueness (the full key is still what backs
        # `uri`, which is what content is actually addressed/read by).
        return ArtifactRef(
            artifact_type="JsonArtifact",
            schema_version="1.0",
            artifact_id=sha256_digest(idempotency_key.encode("utf-8")),
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


@dataclass(frozen=True, slots=True)
class ControlPlane:
    """The durable-infrastructure ports a case runs against.

    `durable` is False when any part fell back to an in-process stand-in, so
    a caller can say so out loud instead of a run silently looking
    production-grade while its evidence lives in RAM.
    """

    artifacts: Any
    checkpointer: Any
    intents: Any
    policy: Any
    telemetry: Any
    policy_version: str
    durable: bool
    notes: tuple[str, ...]


_DURABLE_CONNECT_TIMEOUT_SECONDS = 5.0
_REACHABILITY_PROBE_SECONDS = 0.5


def _endpoint_reachable(url: str, *, default_port: int) -> bool:
    """Cheap TCP probe before building a durable adapter in `auto` mode.

    Constructing `PostgresIntentLedger` against a dead host costs a five
    second pool timeout and leaves the pool's worker thread behind, and
    `S3ArtifactStore` has no healthcheck at all (boto3 connects lazily, so
    construction succeeds and the *first artifact write* is what fails).
    Probing the socket first keeps the fallback fast and honest. Only used
    for `auto`; `durable` builds for real so its errors surface verbatim.
    """

    parsed = urlsplit(url if "//" in url else f"//{url}")
    host = parsed.hostname
    if not host:
        return False
    try:
        with socket.create_connection(
            (host, parsed.port or default_port), timeout=_REACHABILITY_PROBE_SECONDS
        ):
            return True
    except OSError:
        return False


def build_control_plane(*, service_name: str = "production-optimizer") -> ControlPlane:
    """Choose real adapters wherever the environment configures them.

    Every runnable entrypoint used to hardcode `MemoryArtifactStore` +
    `MemoryIntentLedger` + `AllowPolicy`, so the Postgres/S3/OTel adapters --
    implemented and tested -- were unreachable from anything you could
    actually run, and no run could survive its own process (REQ-AUDIT-001,
    REQ-OPS-001).

    `OPTIMIZER_CONTROL_PLANE` selects the posture, because merely *finding*
    a DSN in the environment is not consent: `.env.example` ships one, so
    presence alone would turn every developer's first run into a 30-second
    connection timeout against a database they never started.

    * `auto` (default): use each durable adapter the environment configures
      and that actually answers; otherwise fall back, loudly, per port.
    * `durable`: require them. A misconfigured or unreachable dependency
      raises instead of silently degrading -- what a production deployment
      wants.
    * `memory`: in-process everything, no connection attempts.

    The policy port is the exception: it upgrades in every mode. Fail-open
    authorization is not a reasonable local convenience, and
    `build_bootstrap_policy` needs no infrastructure.
    """

    mode = os.environ.get("OPTIMIZER_CONTROL_PLANE", "auto").strip().lower() or "auto"
    if mode not in {"auto", "durable", "memory"}:
        raise ValueError(
            f"OPTIMIZER_CONTROL_PLANE must be one of auto/durable/memory, got {mode!r}"
        )

    notes: list[str] = []
    policy_version = os.environ.get("OPTIMIZER_POLICY_VERSION", "bootstrap-v1")

    intents, intents_durable = _build_intents(mode, notes)
    checkpointer, checkpointer_durable = _build_checkpointer(mode, notes)
    artifacts, artifacts_durable = _build_artifacts(mode, notes)
    telemetry = _build_telemetry(mode, notes, service_name=service_name)

    from production_optimizer.adapters.production import build_bootstrap_policy

    policy = build_bootstrap_policy(policy_version=policy_version)
    notes.append(f"policy: DeterministicPythonPolicy(fail-closed, {policy_version})")

    return ControlPlane(
        artifacts=artifacts,
        checkpointer=checkpointer,
        intents=intents,
        policy=policy,
        telemetry=telemetry,
        policy_version=policy_version,
        durable=(intents_durable and artifacts_durable and checkpointer_durable),
        notes=tuple(notes),
    )


def _build_intents(mode: str, notes: list[str]) -> tuple[Any, bool]:
    dsn = os.environ.get("OPTIMIZER_DATABASE_DSN", "").strip()
    if mode == "memory" or not dsn:
        if mode == "durable":
            raise RuntimeError(
                "OPTIMIZER_CONTROL_PLANE=durable requires OPTIMIZER_DATABASE_DSN to be set"
            )
        notes.append("intents: in-memory (set OPTIMIZER_DATABASE_DSN for a durable ledger)")
        return MemoryIntentLedger(), False

    if mode == "auto" and not _endpoint_reachable(dsn, default_port=5432):
        notes.append("intents: in-memory (Postgres not answering at OPTIMIZER_DATABASE_DSN)")
        return MemoryIntentLedger(), False

    from production_optimizer.adapters.production import PostgresIntentLedger

    try:
        ledger = PostgresIntentLedger(
            dsn, connect_timeout_seconds=_DURABLE_CONNECT_TIMEOUT_SECONDS
        )
    except Exception as error:
        if mode == "durable":
            raise RuntimeError(f"OPTIMIZER_DATABASE_DSN is unreachable: {error}") from error
        notes.append(f"intents: in-memory (Postgres unreachable: {type(error).__name__})")
        return MemoryIntentLedger(), False

    notes.append("intents: PostgresIntentLedger")
    return ledger, True


def _build_checkpointer(mode: str, notes: list[str]) -> tuple[Any | None, bool]:
    dsn = os.environ.get("OPTIMIZER_DATABASE_DSN", "").strip()
    if mode == "memory" or not dsn:
        if mode == "durable":
            raise RuntimeError(
                "OPTIMIZER_CONTROL_PLANE=durable requires OPTIMIZER_DATABASE_DSN to be set"
            )
        notes.append("checkpoints: disabled (set OPTIMIZER_DATABASE_DSN for durable resume)")
        return None, False

    if mode == "auto" and not _endpoint_reachable(dsn, default_port=5432):
        notes.append("checkpoints: disabled (Postgres not answering)")
        return None, False

    from production_optimizer.adapters.production import PostgresCheckpointProvider

    provider = PostgresCheckpointProvider(
        dsn, connect_timeout_seconds=_DURABLE_CONNECT_TIMEOUT_SECONDS
    )
    try:
        provider.setup()
    except Exception as error:
        provider.close()
        if mode == "durable":
            raise RuntimeError(f"checkpoint provider is unreachable: {error}") from error
        notes.append(f"checkpoints: disabled (Postgres unreachable: {type(error).__name__})")
        return None, False

    notes.append("checkpoints: PostgresCheckpointProvider")
    return provider.checkpointer(), True


def _build_artifacts(mode: str, notes: list[str]) -> tuple[Any, bool]:
    endpoint = os.environ.get("OPTIMIZER_ARTIFACT_ENDPOINT", "").strip()
    bucket = os.environ.get("OPTIMIZER_ARTIFACT_BUCKET", "").strip()
    access_key = os.environ.get("OPTIMIZER_ARTIFACT_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("OPTIMIZER_ARTIFACT_SECRET_KEY", "").strip()
    configured = bool(endpoint and bucket and access_key and secret_key)

    if mode == "memory" or not configured:
        if mode == "durable":
            raise RuntimeError(
                "OPTIMIZER_CONTROL_PLANE=durable requires OPTIMIZER_ARTIFACT_ENDPOINT/"
                "BUCKET/ACCESS_KEY/SECRET_KEY to be set"
            )
        notes.append("artifacts: in-memory (set OPTIMIZER_ARTIFACT_* for a durable store)")
        return MemoryArtifactStore(), False

    if mode == "auto" and not _endpoint_reachable(endpoint, default_port=443):
        notes.append("artifacts: in-memory (object store not answering at "
                     "OPTIMIZER_ARTIFACT_ENDPOINT)")
        return MemoryArtifactStore(), False

    from production_optimizer.adapters.production import S3ArtifactStore

    try:
        store = S3ArtifactStore(
            endpoint_url=endpoint, bucket=bucket, access_key=access_key, secret_key=secret_key
        )
    except Exception as error:
        if mode == "durable":
            raise RuntimeError(f"artifact store is unusable: {error}") from error
        notes.append(f"artifacts: in-memory (S3 unusable: {type(error).__name__})")
        return MemoryArtifactStore(), False

    notes.append(f"artifacts: S3ArtifactStore({bucket})")
    return store, True


def _build_telemetry(mode: str, notes: list[str], *, service_name: str) -> Any:
    otel_endpoint = os.environ.get("OPTIMIZER_OTEL_ENDPOINT", "").strip()
    if mode == "memory" or not otel_endpoint:
        notes.append("telemetry: disabled (set OPTIMIZER_OTEL_ENDPOINT to emit spans)")
        return None

    if mode == "auto" and not _endpoint_reachable(otel_endpoint, default_port=4317):
        # The OTLP exporter retries in a background thread and prints a wall
        # of connection errors over the actual run output, so a collector
        # that is configured but not running must be detected up front.
        notes.append("telemetry: disabled (no collector answering at OPTIMIZER_OTEL_ENDPOINT)")
        return None

    from production_optimizer.adapters.production import OtelTelemetryPort

    try:
        telemetry = OtelTelemetryPort(otlp_endpoint=otel_endpoint, service_name=service_name)
    except Exception as error:
        if mode == "durable":
            raise RuntimeError(f"telemetry endpoint is unusable: {error}") from error
        notes.append(f"telemetry: disabled (OTel unusable: {type(error).__name__})")
        return None

    notes.append("telemetry: OtelTelemetryPort")
    return telemetry


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


_PROVIDER_CHOICES = (
    "auto",
    "anthropic",
    "openai",
    "gemini",
    "deepseek",
    "ollama",
    "local-scripted",
)
_REAL_PROVIDER_CHOICES = ("anthropic", "openai", "gemini", "deepseek", "ollama")
_PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-3.6-flash",
    "deepseek": "deepseek-chat",
    "ollama": "llama3.2",
}
_PROVIDER_MODEL_ENV = {
    "anthropic": "ANTHROPIC_MODEL_ID",
    "openai": "OPENAI_MODEL_ID",
    "gemini": "GEMINI_MODEL_ID",
    "deepseek": "DEEPSEEK_MODEL_ID",
    "ollama": "OLLAMA_MODEL_ID",
}
_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
_PROVIDER_SECRET_REF = {
    "anthropic": "anthropic-api-key",
    "openai": "openai-api-key",
    "gemini": "gemini-api-key",
    "deepseek": "deepseek-api-key",
}
_PROVIDER_BASE_URL_ENV = {
    "deepseek": "DEEPSEEK_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
}
_PROVIDER_DEFAULT_BASE_URL = {
    "deepseek": "https://api.deepseek.com",
    "ollama": "http://localhost:11434/v1",
}


def add_model_runtime_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(
        "model runtime",
        "Controls the LLM provider/model used by A1/A2/A3/Sxx model calls.",
    )
    group.add_argument(
        "--model-provider",
        choices=_PROVIDER_CHOICES,
        default="auto",
        help=(
            "Provider to use for model calls. Default 'auto' uses --model-provider-order "
            "or OPTIMIZER_MODEL_PROVIDER_ORDER and available credentials."
        ),
    )
    group.add_argument(
        "--model-id",
        default=None,
        help="Provider-specific model id for --model-provider, e.g. gemini-3.6-flash.",
    )
    group.add_argument(
        "--model-base-url",
        default=None,
        help="Base URL for OpenAI-compatible providers such as ollama/deepseek.",
    )
    group.add_argument(
        "--model-provider-order",
        default=None,
        help=(
            "Comma-separated provider order for auto mode, e.g. gemini,openai,ollama. "
            "Overrides OPTIMIZER_MODEL_PROVIDER_ORDER for this run."
        ),
    )
def model_selection_kwargs_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "provider_name": args.model_provider,
        "model_id": args.model_id,
        "base_url": args.model_base_url,
        "provider_order": args.model_provider_order,
    }


def select_model_provider(
    *,
    tenant_id: str,
    thread_id: str | None = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    base_url: str | None = None,
    provider_order: str | None = None,
) -> tuple[Any, str]:
    """Select exactly one approved model provider -- no retry, no fallback.

    A failed `complete()` call raises `ModelProviderError` immediately (see
    `adapters/production/model_provider_errors.py`); this function's only
    job is picking *which* provider/model to construct, once, up front.
    `--model-provider auto` (the default) walks `--model-provider-order`/
    `OPTIMIZER_MODEL_PROVIDER_ORDER` and picks the first one with a usable
    credential (or a reachable local Ollama) -- that is a one-time startup
    choice, not an automatic switch made after a call fails at runtime.
    """

    del thread_id  # kept for call-site compatibility; unused without a gateway

    if provider_name == "local-scripted":
        print("[model] using LocalScriptedModelProvider because --model-provider local-scripted")
        return LocalScriptedModelProvider(), "local-scripted"

    candidate = _select_model_provider_candidate(
        tenant_id=tenant_id,
        provider_name=provider_name or "auto",
        model_id=model_id,
        base_url=base_url,
        provider_order=provider_order,
    )
    if candidate is None:
        print(
            "[model] no ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY/DEEPSEEK_API_KEY set "
            "and no local Ollama server reachable -- using LocalScriptedModelProvider "
            "(NOT a real model). Set one of those env vars (see .env.example) to see real "
            "LLM output."
        )
        return LocalScriptedModelProvider(), "local-scripted"

    _, provider, selected_model_id = candidate
    return provider, selected_model_id


def _select_model_provider_candidate(
    *,
    tenant_id: str,
    provider_name: str,
    model_id: str | None,
    base_url: str | None,
    provider_order: str | None,
) -> tuple[str, Any, str] | None:
    if provider_name != "auto":
        candidate = _provider_candidate(
            tenant_id=tenant_id,
            provider_name=provider_name,
            model_id=model_id,
            base_url=base_url,
            explicit=True,
        )
        assert candidate is not None
        return candidate

    order_source = provider_order or os.environ.get(
        "OPTIMIZER_MODEL_PROVIDER_ORDER", "anthropic,openai,gemini,deepseek,ollama"
    )
    order = [item.strip().lower() for item in order_source.split(",") if item.strip()]
    for ordered_provider in order:
        if ordered_provider not in _REAL_PROVIDER_CHOICES:
            continue
        candidate = _provider_candidate(
            tenant_id=tenant_id,
            provider_name=ordered_provider,
            model_id=model_id if ordered_provider == order[0] else None,
            base_url=base_url if ordered_provider == order[0] else None,
            explicit=False,
        )
        if candidate is not None:
            return candidate
    return None


def _provider_candidate(
    *,
    tenant_id: str,
    provider_name: str,
    model_id: str | None,
    base_url: str | None,
    explicit: bool,
) -> tuple[str, Any, str] | None:
    if provider_name not in _REAL_PROVIDER_CHOICES:
        if explicit:
            raise ValueError(f"unsupported model provider: {provider_name}")
        return None

    from production_optimizer.adapters.production import GenericModelProvider

    selected_model = (
        model_id
        or os.environ.get(_PROVIDER_MODEL_ENV[provider_name])
        or _PROVIDER_DEFAULT_MODELS[provider_name]
    )
    selected_base_url = (
        base_url
        or os.environ.get(_PROVIDER_BASE_URL_ENV.get(provider_name, ""))
        or _PROVIDER_DEFAULT_BASE_URL.get(provider_name)
    )

    if provider_name == "ollama":
        if not explicit and not _ollama_reachable(selected_base_url or "http://localhost:11434/v1"):
            return None
        print(
            f"[model] approved GenericModelProvider(provider=ollama), "
            f"model={selected_model}, base_url={selected_base_url}"
        )
        return (
            "ollama",
            GenericModelProvider(provider="ollama", base_url=selected_base_url),
            selected_model,
        )

    api_key = os.environ.get(_PROVIDER_KEY_ENV[provider_name])
    if not api_key:
        if explicit:
            raise RuntimeError(
                f"--model-provider {provider_name} requires {_PROVIDER_KEY_ENV[provider_name]}"
            )
        return None
    print(
        f"[model] approved GenericModelProvider(provider={provider_name}), "
        f"model={selected_model}"
    )
    return (
        provider_name,
        GenericModelProvider(
            provider=provider_name,  # type: ignore[arg-type]
            secrets=EnvSecretsBroker(api_key),
            tenant_id=tenant_id,
            secret_ref=_PROVIDER_SECRET_REF[provider_name],
            base_url=selected_base_url,
        ),
        selected_model,
    )


def _ollama_reachable(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or 11434), timeout=0.5
        ):
            return True
    except OSError:
        return False


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
