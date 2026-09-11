from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum

from pydantic import Field

from production_optimizer.contracts.base import ContractModel


class ProbeStatus(StrEnum):
    UP = "up"
    DOWN = "down"


class HealthReport(ContractModel):
    status: ProbeStatus
    checks: dict[str, ProbeStatus]
    version: str = Field(min_length=1)


class ReadinessProbe:
    """Framework-neutral readiness aggregation for a future HTTP adapter."""

    def __init__(self, checks: Mapping[str, Callable[[], bool]], *, version: str) -> None:
        self._checks = dict(checks)
        self._version = version

    def inspect(self) -> HealthReport:
        results = {
            name: ProbeStatus.UP if _safe_check(check) else ProbeStatus.DOWN
            for name, check in sorted(self._checks.items())
        }
        all_available = all(value == ProbeStatus.UP for value in results.values())
        overall = ProbeStatus.UP if all_available else ProbeStatus.DOWN
        return HealthReport(status=overall, checks=results, version=self._version)


def _safe_check(check: Callable[[], bool]) -> bool:
    try:
        return check()
    except Exception:
        return False
