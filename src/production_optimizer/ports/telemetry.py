from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.platform import TelemetryEvent


class TelemetryPort(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...

    def flush(self, *, timeout_seconds: float) -> None: ...
