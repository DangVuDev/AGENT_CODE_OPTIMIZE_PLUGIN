"""Production handlers for the A2 business stage.

Each business node lives in ``nodes/<node-id>_<responsibility>.py``. The
compatibility exports below preserve the original application API.
"""

from . import shared as _shared
from .registry import (
    A2_BLOCKED_NODES,
    NODE_HANDLERS,
    build_a2_registrations,
    build_a2_runtime,
)

# Preserve explicitly imported private helpers used by focused unit tests.
globals().update(
    {name: value for name, value in vars(_shared).items() if not name.startswith("__")}
)

__all__ = [
    "A2_BLOCKED_NODES",
    "NODE_HANDLERS",
    "build_a2_registrations",
    "build_a2_runtime",
]
