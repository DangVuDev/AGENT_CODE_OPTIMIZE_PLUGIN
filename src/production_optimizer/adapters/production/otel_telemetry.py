from __future__ import annotations

import os
from collections.abc import Callable

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from production_optimizer.contracts.platform import TelemetryEvent

AttributeValue = str | int | float | bool


class OtelTelemetryPort:
    """``TelemetryPort`` backed by an OpenTelemetry tracer.

    Each ``emit`` starts and immediately ends a span named after
    ``event.node_id``, with scalar attributes for ``case_id``, ``thread_id``,
    ``node_id``, ``attempt``, ``result_code``, and every key in
    ``event.attributes`` prefixed ``attr.`` to keep free-form attributes
    namespaced away from the fixed event fields.

    Safety invariant: ``TelemetryEvent.attributes`` is typed
    ``dict[str, str | int | float | bool]`` by the contract, so this adapter
    can never receive raw source code, evidence payloads, or secrets through
    it -- attributes are scalar-only by construction, per the project's
    observability rules. Do not widen that contract type without revisiting
    this invariant.

    Production deployments must supply real TLS/auth configuration for the
    OTLP exporter; the default constructed here uses ``insecure=True``,
    which is only appropriate for a local collector. Hardening that is out
    of scope for this adapter.

    For testability without a live collector, the exporter is built via an
    injectable ``_exporter_factory`` seam (defaulting to a real
    ``OTLPSpanExporter``); tests can inject one backed by the SDK's
    in-memory exporter instead.
    """

    def __init__(
        self,
        *,
        otlp_endpoint: str,
        service_name: str = "production-optimizer",
        _exporter_factory: Callable[[], SpanExporter] | None = None,
    ) -> None:
        if _exporter_factory is None and not otlp_endpoint.strip():
            raise ValueError("otlp_endpoint must not be empty")
        if not service_name.strip():
            raise ValueError("service_name must not be empty")

        factory = _exporter_factory or (
            lambda: OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        )
        self._provider = TracerProvider()
        self._provider.add_span_processor(BatchSpanProcessor(factory()))
        self._tracer = self._provider.get_tracer(service_name)

    @classmethod
    def from_environment(cls) -> OtelTelemetryPort:
        otlp_endpoint = os.environ.get("OPTIMIZER_OTEL_ENDPOINT", "").strip()
        if not otlp_endpoint:
            raise ValueError(
                "required environment variable is missing: OPTIMIZER_OTEL_ENDPOINT"
            )
        return cls(otlp_endpoint=otlp_endpoint)

    def emit(self, event: TelemetryEvent) -> None:
        attributes: dict[str, AttributeValue] = {
            "case_id": event.case_id,
            "thread_id": event.thread_id,
            "node_id": event.node_id,
            "attempt": event.attempt,
            "result_code": event.result_code,
        }
        for key, value in event.attributes.items():
            attributes[f"attr.{key}"] = value
        with self._tracer.start_as_current_span(event.node_id, attributes=attributes):
            pass

    def flush(self, *, timeout_seconds: float) -> None:
        self._provider.force_flush(timeout_millis=int(timeout_seconds * 1000))

    def shutdown(self) -> None:
        self._provider.shutdown()

    def close(self) -> None:
        self.shutdown()
