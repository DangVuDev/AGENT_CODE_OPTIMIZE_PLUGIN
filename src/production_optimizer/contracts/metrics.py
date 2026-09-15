"""Typed metric semantics shared by intake, evidence, and analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import ContractModel

MetricValueKind = Literal["number", "boolean", "distribution"]
MetricAggregation = Literal["mean", "p50", "p95", "p99", "sum", "rate", "maximum", "verdict"]
MetricDirection = Literal["minimize", "maximize", "target"]
MetricComparison = Literal["threshold", "relative", "statistical", "guardrail"]


class MetricDefinition(ContractModel):
    """Immutable interpretation of one metric identifier."""

    metric_id: str = Field(min_length=1, max_length=255)
    schema_version: str = Field(default="1.0", min_length=1, max_length=32)
    value_kind: MetricValueKind
    canonical_unit: str = Field(min_length=1, max_length=100)
    default_direction: MetricDirection
    aggregation: MetricAggregation
    comparison: MetricComparison = "threshold"
    accepted_source_types: set[str] = Field(min_length=1)
    minimum_samples: int = Field(default=1, ge=1, le=10_000)


__all__ = [
    "MetricAggregation",
    "MetricComparison",
    "MetricDefinition",
    "MetricDirection",
    "MetricValueKind",
]
