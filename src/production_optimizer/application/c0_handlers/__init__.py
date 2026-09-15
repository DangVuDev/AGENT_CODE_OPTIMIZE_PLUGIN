"""Production handlers for the C0 business stage.

Each business node lives in ``nodes/<node-id>_<responsibility>.py``. The
compatibility exports below preserve the original application API.
"""

from . import shared as _shared
from .registry import NODE_HANDLERS

# Preserve explicitly imported private helpers used by focused unit tests.
globals().update(
    {name: value for name, value in vars(_shared).items() if not name.startswith("__")}
)
build_c0_registrations = _shared.build_c0_registrations
build_c0_runtime = _shared.build_c0_runtime

__all__ = [
    "NODE_HANDLERS",
    "build_c0_registrations",
    "build_c0_runtime",
]
