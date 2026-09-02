"""A FidelityTarget for the vendor shipped in this distribution.

On the test side of the tree, not inside the package: ``vendorfake.fidelity``
may not import a vendor, and this file does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import vendorfake.square  # noqa: F401 -- the vendor this target points at
from vendorfake import create_unit
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.fidelity.runner import FidelityTarget

VENDOR = "square"
ANCHOR = "vendorfake.square.fidelity"
DEFAULT_PROFILE = "full"


@contextmanager
def open_unit(profile: str | None) -> Iterator[Unit]:
    """One fresh unit per case, stopped on exit. ``warn`` keeps the run readable."""
    unit = create_unit(vendor=VENDOR, profile=profile or DEFAULT_PROFILE, sink=MemorySink(), logger=JsonLogger("warn"))
    try:
        yield unit
    finally:
        unit.stop()


def square_target() -> FidelityTarget:
    return FidelityTarget(name=VENDOR, anchor=ANCHOR, open_unit=open_unit, default_profile=DEFAULT_PROFILE)
