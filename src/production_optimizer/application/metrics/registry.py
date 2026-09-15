"""Versioned built-in metric definitions and explicit custom fallback."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from production_optimizer.contracts.metrics import MetricDefinition, MetricDirection


def _definition(
    metric_id: str,
    *,
    value_kind: str,
    unit: str,
    direction: str,
    aggregation: str,
    sources: set[str],
    minimum_samples: int = 1,
    comparison: str = "threshold",
) -> MetricDefinition:
    return MetricDefinition.model_validate(
        {
            "metric_id": metric_id,
            "value_kind": value_kind,
            "canonical_unit": unit,
            "default_direction": direction,
            "aggregation": aggregation,
            "comparison": comparison,
            "accepted_source_types": sources,
            "minimum_samples": minimum_samples,
        }
    )


_BUILTINS = (
    _definition(
        "p50_latency_ms",
        value_kind="distribution",
        unit="ms",
        direction="minimize",
        aggregation="p50",
        sources={"benchmark", "telemetry"},
        minimum_samples=3,
        comparison="statistical",
    ),
    _definition(
        "p95_latency_ms",
        value_kind="distribution",
        unit="ms",
        direction="minimize",
        aggregation="p95",
        sources={"benchmark", "telemetry"},
        minimum_samples=3,
        comparison="statistical",
    ),
    _definition(
        "p99_latency_ms",
        value_kind="distribution",
        unit="ms",
        direction="minimize",
        aggregation="p99",
        sources={"benchmark", "telemetry"},
        minimum_samples=3,
        comparison="statistical",
    ),
    _definition(
        "throughput_rps",
        value_kind="number",
        unit="requests_per_second",
        direction="maximize",
        aggregation="rate",
        sources={"benchmark", "telemetry"},
        minimum_samples=3,
    ),
    _definition(
        "peak_memory_mb",
        value_kind="number",
        unit="MiB",
        direction="minimize",
        aggregation="maximum",
        sources={"telemetry", "profiler"},
    ),
    _definition(
        "cpu_utilization_percent",
        value_kind="distribution",
        unit="percent",
        direction="minimize",
        aggregation="p95",
        sources={"telemetry", "profiler"},
        minimum_samples=3,
    ),
    _definition(
        "input_tokens",
        value_kind="number",
        unit="tokens",
        direction="minimize",
        aggregation="sum",
        sources={"evaluator", "telemetry"},
    ),
    _definition(
        "output_tokens",
        value_kind="number",
        unit="tokens",
        direction="minimize",
        aggregation="sum",
        sources={"evaluator", "telemetry"},
    ),
    _definition(
        "total_tokens",
        value_kind="number",
        unit="tokens",
        direction="minimize",
        aggregation="sum",
        sources={"evaluator", "telemetry"},
    ),
    _definition(
        "cost_usd",
        value_kind="number",
        unit="USD",
        direction="minimize",
        aggregation="sum",
        sources={"evaluator", "telemetry"},
    ),
    _definition(
        "quality_score",
        value_kind="number",
        unit="score",
        direction="maximize",
        aggregation="mean",
        sources={"evaluator", "judge"},
        minimum_samples=3,
        comparison="statistical",
    ),
    _definition(
        "correctness",
        value_kind="boolean",
        unit="exit_code",
        direction="target",
        aggregation="verdict",
        sources={"test", "evaluator"},
        comparison="guardrail",
    ),
    _definition(
        "unit_command_result",
        value_kind="number",
        unit="exit_code",
        direction="target",
        aggregation="verdict",
        sources={"test"},
        comparison="guardrail",
    ),
)

BUILTIN_METRICS: Mapping[str, MetricDefinition] = MappingProxyType(
    {definition.metric_id: definition for definition in _BUILTINS}
)


def resolve_metric(
    metric_id: str,
    *,
    declared_unit: str | None = None,
    declared_direction: MetricDirection | None = None,
) -> MetricDefinition:
    """Resolve an exact built-in or construct a non-inferred custom metric."""

    definition = BUILTIN_METRICS.get(metric_id)
    if definition is not None:
        return definition
    return MetricDefinition(
        metric_id=metric_id,
        value_kind="number",
        canonical_unit=declared_unit or "scalar",
        default_direction=declared_direction or "target",
        aggregation="mean",
        comparison="threshold",
        accepted_source_types={"benchmark", "telemetry", "evaluator"},
        minimum_samples=1,
    )


__all__ = ["BUILTIN_METRICS", "resolve_metric"]
