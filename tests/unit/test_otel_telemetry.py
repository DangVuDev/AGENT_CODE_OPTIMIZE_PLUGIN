from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from production_optimizer.adapters.production.otel_telemetry import OtelTelemetryPort
from production_optimizer.contracts.platform import TelemetryEvent


def _port_with_in_memory_exporter() -> tuple[OtelTelemetryPort, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    port = OtelTelemetryPort(
        otlp_endpoint="",
        _exporter_factory=lambda: exporter,
    )
    return port, exporter


def test_emit_produces_a_span_named_after_the_node_with_flattened_attributes() -> None:
    port, exporter = _port_with_in_memory_exporter()
    event = TelemetryEvent(
        case_id="case-1",
        thread_id="thread-1",
        node_id="a1_ingest_intake",
        attempt=2,
        result_code="OK",
        attributes={"strategy": "cache", "score": 0.5, "retries": 1, "ok": True},
    )

    port.emit(event)
    port.flush(timeout_seconds=1.0)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "a1_ingest_intake"
    assert span.attributes is not None
    assert span.attributes["case_id"] == "case-1"
    assert span.attributes["thread_id"] == "thread-1"
    assert span.attributes["node_id"] == "a1_ingest_intake"
    assert span.attributes["attempt"] == 2
    assert span.attributes["result_code"] == "OK"
    assert span.attributes["attr.strategy"] == "cache"
    assert span.attributes["attr.score"] == 0.5
    assert span.attributes["attr.retries"] == 1
    assert span.attributes["attr.ok"] is True


def test_emit_twice_produces_two_finished_spans() -> None:
    port, exporter = _port_with_in_memory_exporter()
    event = TelemetryEvent(
        case_id="case-1",
        thread_id="thread-1",
        node_id="a1_ingest_intake",
        attempt=1,
        result_code="OK",
        attributes={},
    )

    port.emit(event)
    port.emit(event)
    port.flush(timeout_seconds=1.0)

    assert len(exporter.get_finished_spans()) == 2


def test_constructor_rejects_empty_endpoint_without_injected_factory() -> None:
    with pytest.raises(ValueError, match="otlp_endpoint"):
        OtelTelemetryPort(otlp_endpoint="  ")


def test_shutdown_and_close_do_not_raise() -> None:
    port, _exporter = _port_with_in_memory_exporter()
    port.close()
