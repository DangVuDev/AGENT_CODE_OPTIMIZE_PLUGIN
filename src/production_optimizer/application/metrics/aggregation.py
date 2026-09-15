"""Deterministic selection of decision values from common summaries."""

from __future__ import annotations

from production_optimizer.contracts.a2 import MetricAggregate
from production_optimizer.contracts.metrics import MetricAggregation


def select_aggregate_value(aggregate: MetricAggregate, strategy: MetricAggregation) -> float:
    """Return the statistic whose semantics match ``strategy``."""

    if strategy in {"mean", "rate"}:
        return aggregate.mean
    if strategy == "p50":
        if aggregate.percentile_50 is None:
            raise ValueError(f"metric {aggregate.metric_id!r} has no p50")
        return aggregate.percentile_50
    if strategy == "p95":
        if aggregate.percentile_95 is None:
            raise ValueError(f"metric {aggregate.metric_id!r} has no p95")
        return aggregate.percentile_95
    if strategy in {"p99", "maximum", "verdict"}:
        return aggregate.maximum
    if strategy == "sum":
        return aggregate.mean * aggregate.count
    raise ValueError(f"unsupported aggregation strategy {strategy!r}")


__all__ = ["select_aggregate_value"]
