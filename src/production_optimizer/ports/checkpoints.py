from __future__ import annotations

from typing import Any, Protocol


class CheckpointProvider(Protocol):
    """Composition boundary for a LangGraph checkpointer instance."""

    def setup(self) -> None: ...

    def checkpointer(self) -> Any: ...

    def healthcheck(self) -> bool: ...

    def close(self) -> None: ...
