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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import sha256_digest
from production_optimizer.contracts.platform import (
    DeferredModelCallRecord,
    DeferredModelCallStatus,
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


class MemoryModelCallDeferralStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], DeferredModelCallRecord] = {}

    def defer(self, record: DeferredModelCallRecord) -> DeferredModelCallRecord:
        existing = self._records.get((record.tenant_id, record.deferral_id))
        if existing is not None and existing.status is DeferredModelCallStatus.SUCCEEDED:
            return existing
        self._records[(record.tenant_id, record.deferral_id)] = record
        return record

    def get(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord | None:
        return self._records.get((tenant_id, deferral_id))

    def claim_due(
        self, *, tenant_id: str, lease_owner: str, lease_seconds: int, limit: int
    ) -> list[DeferredModelCallRecord]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = datetime.now(UTC)
        claimed: list[DeferredModelCallRecord] = []
        for key, record in sorted(
            self._records.items(), key=lambda item: item[1].available_at
        ):
            if len(claimed) >= limit:
                break
            if record.tenant_id != tenant_id:
                continue
            due_scheduled = (
                record.status is DeferredModelCallStatus.SCHEDULED
                and record.available_at <= now
            )
            expired_running = (
                record.status is DeferredModelCallStatus.RUNNING
                and record.lease_expires_at is not None
                and record.lease_expires_at <= now
            )
            if not due_scheduled and not expired_running:
                continue
            updated = record.model_copy(
                update={
                    "status": DeferredModelCallStatus.RUNNING,
                    "lease_owner": lease_owner,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "attempts": record.attempts + 1,
                    "updated_at": now,
                }
            )
            self._records[key] = updated
            claimed.append(updated)
        return claimed

    def mark_succeeded(self, *, tenant_id: str, deferral_id: str) -> DeferredModelCallRecord:
        return self._update_status(
            tenant_id=tenant_id,
            deferral_id=deferral_id,
            status=DeferredModelCallStatus.SUCCEEDED,
        )

    def mark_failed(
        self, *, tenant_id: str, deferral_id: str, error_ref: str
    ) -> DeferredModelCallRecord:
        return self._update_status(
            tenant_id=tenant_id,
            deferral_id=deferral_id,
            status=DeferredModelCallStatus.FAILED,
            last_error_ref=error_ref,
        )

    def healthcheck(self) -> bool:
        return True

    def _update_status(
        self,
        *,
        tenant_id: str,
        deferral_id: str,
        status: DeferredModelCallStatus,
        last_error_ref: str | None = None,
    ) -> DeferredModelCallRecord:
        key = (tenant_id, deferral_id)
        record = self._records.get(key)
        if record is None:
            raise ValueError(f"no model call deferral found for {tenant_id=} {deferral_id=}")
        updated = record.model_copy(
            update={
                "status": status,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error_ref": last_error_ref,
                "updated_at": datetime.now(UTC),
            }
        )
        self._records[key] = updated
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
    model_deferrals: Any
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
    model_deferrals, model_deferrals_durable = _build_model_deferrals(mode, notes)
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
        model_deferrals=model_deferrals,
        policy=policy,
        telemetry=telemetry,
        policy_version=policy_version,
        durable=(
            intents_durable
            and artifacts_durable
            and model_deferrals_durable
            and checkpointer_durable
        ),
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


def _build_model_deferrals(mode: str, notes: list[str]) -> tuple[Any, bool]:
    dsn = os.environ.get("OPTIMIZER_DATABASE_DSN", "").strip()
    if mode == "memory" or not dsn:
        if mode == "durable":
            raise RuntimeError(
                "OPTIMIZER_CONTROL_PLANE=durable requires OPTIMIZER_DATABASE_DSN to be set"
            )
        notes.append("model deferrals: in-memory (set OPTIMIZER_DATABASE_DSN for durable retry)")
        return MemoryModelCallDeferralStore(), False

    if mode == "auto" and not _endpoint_reachable(dsn, default_port=5432):
        notes.append("model deferrals: in-memory (Postgres not answering)")
        return MemoryModelCallDeferralStore(), False

    from production_optimizer.adapters.production import PostgresModelCallDeferralStore

    try:
        store = PostgresModelCallDeferralStore(
            dsn, connect_timeout_seconds=_DURABLE_CONNECT_TIMEOUT_SECONDS
        )
    except Exception as error:
        if mode == "durable":
            raise RuntimeError(f"model deferral store is unreachable: {error}") from error
        notes.append(
            f"model deferrals: in-memory (Postgres unreachable: {type(error).__name__})"
        )
        return MemoryModelCallDeferralStore(), False

    notes.append("model deferrals: PostgresModelCallDeferralStore")
    return store, True


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


def select_model_provider(
    *, tenant_id: str, thread_id: str | None = None, deferrals: Any | None = None
) -> tuple[Any, str]:
    """Select approved model providers behind a resilient gateway.

    Each real provider first gets a bounded `RetryingModelProvider`; the
    `ResilientModelGateway` above those wrappers opens a circuit after an
    exhausted transient failure, then tries the next configured provider.
    When none can serve, it raises `DeferredModelCallError`, which CLI/UI
    entrypoints can surface as a clean retry-later halt.
    """

    raw_candidates = _select_raw_model_provider_candidates(tenant_id=tenant_id)
    if not raw_candidates:
        print(
            "[model] no ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY/DEEPSEEK_API_KEY set "
            "and no local Ollama server reachable -- using LocalScriptedModelProvider "
            "(NOT a real model). Set one of those env vars (see .env.example) to see real "
            "LLM output."
        )
        return LocalScriptedModelProvider(), "local-scripted"

    from production_optimizer.adapters.production import (
        ModelGatewayEvent,
        ModelProviderCandidate,
        ResilientModelGateway,
        RetryingModelProvider,
    )

    def _announce_retry(
        provider_name: str, attempt: int, delay: float, error: BaseException
    ) -> None:
        print(
            f"[model:{provider_name}] {type(error).__name__} on attempt {attempt} "
            f"({error}); retrying in {delay:.1f}s"
        )

    def _announce_gateway(event: ModelGatewayEvent) -> None:
        if event.event_type not in {"fallback", "fallback_success", "circuit_opened"}:
            return
        print(
            f"[model-gateway] {event.event_type}: {event.provider_name}/"
            f"{event.model_id} {event.detail}"
        )

    candidates = [
        ModelProviderCandidate(
            provider_name=name,
            provider=RetryingModelProvider(
                provider,
                on_retry=lambda attempt, delay, error, provider_name=name: _announce_retry(
                    provider_name, attempt, delay, error
                ),
            ),
            model_id=model_id,
        )
        for name, provider, model_id in raw_candidates
    ]
    primary = candidates[0]
    if len(candidates) == 1:
        print(f"[model-gateway] primary={primary.provider_name}/{primary.model_id}; no fallback")
    else:
        chain = ", ".join(f"{c.provider_name}/{c.model_id}" for c in candidates)
        print(f"[model-gateway] approved provider chain: {chain}")
    return (
        ResilientModelGateway(
            candidates,
            tenant_id=tenant_id,
            thread_id=thread_id,
            deferrals=deferrals,
            on_event=_announce_gateway,
        ),
        primary.model_id,
    )


def _select_raw_model_provider_candidates(*, tenant_id: str) -> list[tuple[str, Any, str]]:
    order = [
        item.strip().lower()
        for item in os.environ.get(
            "OPTIMIZER_MODEL_PROVIDER_ORDER", "anthropic,openai,gemini,deepseek,ollama"
        ).split(",")
        if item.strip()
    ]
    builders = {
        "anthropic": _anthropic_candidate,
        "openai": _openai_candidate,
        "gemini": _gemini_candidate,
        "deepseek": _deepseek_candidate,
        "ollama": _ollama_candidate,
    }
    candidates: list[tuple[str, Any, str]] = []
    for provider_name in order:
        builder = builders.get(provider_name)
        if builder is None:
            continue
        candidate = builder(tenant_id=tenant_id)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _anthropic_candidate(*, tenant_id: str) -> tuple[str, Any, str] | None:
    if not (api_key := os.environ.get("ANTHROPIC_API_KEY")):
        return None
    from production_optimizer.adapters.production import AnthropicModelProvider

    model_id = os.environ.get("ANTHROPIC_MODEL_ID", "claude-sonnet-5")
    print(f"[model] approved AnthropicModelProvider (ANTHROPIC_API_KEY found), model={model_id}")
    return (
        "anthropic",
        AnthropicModelProvider(
            secrets=EnvSecretsBroker(api_key),
            tenant_id=tenant_id,
            secret_ref="anthropic-api-key",
        ),
        model_id,
    )


def _openai_candidate(*, tenant_id: str) -> tuple[str, Any, str] | None:
    if not (api_key := os.environ.get("OPENAI_API_KEY")):
        return None
    from production_optimizer.adapters.production import OpenAIModelProvider

    model_id = os.environ.get("OPENAI_MODEL_ID", "gpt-4o-mini")
    print(f"[model] approved OpenAIModelProvider (OPENAI_API_KEY found), model={model_id}")
    return (
        "openai",
        OpenAIModelProvider(
            secrets=EnvSecretsBroker(api_key), tenant_id=tenant_id, secret_ref="openai-api-key"
        ),
        model_id,
    )


def _gemini_candidate(*, tenant_id: str) -> tuple[str, Any, str] | None:
    if not (api_key := os.environ.get("GEMINI_API_KEY")):
        return None
    from production_optimizer.adapters.production import GeminiModelProvider

    model_id = os.environ.get("GEMINI_MODEL_ID", "gemini-2.5-flash")
    print(f"[model] approved GeminiModelProvider (GEMINI_API_KEY found), model={model_id}")
    return (
        "gemini",
        GeminiModelProvider(
            secrets=EnvSecretsBroker(api_key), tenant_id=tenant_id, secret_ref="gemini-api-key"
        ),
        model_id,
    )


def _deepseek_candidate(*, tenant_id: str) -> tuple[str, Any, str] | None:
    if not (api_key := os.environ.get("DEEPSEEK_API_KEY")):
        return None
    from production_optimizer.adapters.production import DeepSeekModelProvider

    model_id = os.environ.get("DEEPSEEK_MODEL_ID", "deepseek-chat")
    print(f"[model] approved DeepSeekModelProvider (DEEPSEEK_API_KEY found), model={model_id}")
    return (
        "deepseek",
        DeepSeekModelProvider(
            secrets=EnvSecretsBroker(api_key),
            tenant_id=tenant_id,
            secret_ref="deepseek-api-key",
        ),
        model_id,
    )


def _ollama_candidate(*, tenant_id: str) -> tuple[str, Any, str] | None:
    del tenant_id
    ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    parsed = urlsplit(ollama_base_url)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or 11434), timeout=0.5
        ):
            from production_optimizer.adapters.production import OllamaModelProvider

            model_id = os.environ.get("OLLAMA_MODEL_ID", "llama3.2")
            print(
                f"[model] approved OllamaModelProvider (server reachable at "
                f"{ollama_base_url}), model={model_id}"
            )
            return "ollama", OllamaModelProvider(base_url=ollama_base_url), model_id
    except OSError:
        return None


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
