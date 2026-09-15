# pyright: reportPrivateUsage=false
"""Regression tests for evaluator-to-evidence metric semantics."""

from production_optimizer.application.a2_handlers.shared import _canonical_metric_value


def test_boolean_verdict_uses_exit_code_success_convention() -> None:
    assert _canonical_metric_value(True, canonical_unit="exit_code") == 0
    assert _canonical_metric_value(False, canonical_unit="exit_code") == 1


def test_numeric_exit_code_and_non_exit_verdict_are_not_reinterpreted() -> None:
    assert _canonical_metric_value(7, canonical_unit="exit_code") == 7
    assert _canonical_metric_value(True, canonical_unit="verdict") is True
