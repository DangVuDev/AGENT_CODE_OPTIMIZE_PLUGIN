"""Acceptance matrix for metric registry and aggregation strategies."""

from __future__ import annotations

import pytest

from production_optimizer.application.metrics import (
    BUILTIN_METRICS,
    resolve_metric,
    select_aggregate_value,
)
from production_optimizer.contracts.a2 import MetricAggregate
from production_optimizer.contracts.metrics import MetricAggregation


def _aggregate() -> MetricAggregate:
    return MetricAggregate(
        metric_id="example",
        unit="scalar",
        sample_ids=["one", "two", "three", "four"],
        count=4,
        minimum=1,
        maximum=10,
        mean=4,
        percentile_50=3,
        percentile_95=9,
    )


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("mean", 4),
        ("rate", 4),
        ("p50", 3),
        ("p95", 9),
        ("p99", 10),
        ("maximum", 10),
        ("verdict", 10),
        ("sum", 16),
    ],
)
def test_selects_metric_specific_aggregate(strategy: MetricAggregation, expected: float) -> None:
    assert select_aggregate_value(_aggregate(), strategy) == expected


def test_registry_covers_the_product_metric_matrix() -> None:
    expected = {
        "p95_latency_ms",
        "throughput_rps",
        "peak_memory_mb",
        "cpu_utilization_percent",
        "total_tokens",
        "cost_usd",
        "quality_score",
        "correctness",
    }
    assert expected <= BUILTIN_METRICS.keys()
    assert BUILTIN_METRICS["peak_memory_mb"].aggregation == "maximum"
    assert BUILTIN_METRICS["total_tokens"].aggregation == "sum"
    assert BUILTIN_METRICS["correctness"].aggregation == "verdict"


def test_custom_metric_uses_explicit_semantics_without_name_inference() -> None:
    definition = resolve_metric(
        "customer_delight_latency_sounding_name",
        declared_unit="points",
        declared_direction="maximize",
    )
    assert definition.canonical_unit == "points"
    assert definition.default_direction == "maximize"
    assert definition.aggregation == "mean"
    assert definition.value_kind == "number"


def test_missing_percentile_fails_closed() -> None:
    aggregate = _aggregate().model_copy(update={"percentile_95": None})
    with pytest.raises(ValueError, match="has no p95"):
        select_aggregate_value(aggregate, "p95")
