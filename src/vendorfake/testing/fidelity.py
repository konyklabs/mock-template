"""Fidelity targets for the vendors shipped in this distribution, usable from an
installed wheel with no checkout. ``vendorfake.fidelity`` may not import a vendor
or the registry, so the targets live here; ``tests/fidelity/harness.py``
re-exports them rather than defining its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.fidelity.runner import FidelityTarget
from vendorfake.registry import create_unit

__all__ = ["lightspeed_target", "square_target", "toast_target"]

_SQUARE = "square"
_SQUARE_ANCHOR = "vendorfake.square.fidelity"
_TOAST = "toast"
_TOAST_ANCHOR = "vendorfake.toast.fidelity"
_LIGHTSPEED = "lightspeed"
_LIGHTSPEED_ANCHOR = "vendorfake.lightspeed.fidelity"
_DEFAULT_PROFILE = "full"


def _opener(vendor: str, default_profile: str) -> Callable[[str | None], AbstractContextManager[Unit]]:
    @contextmanager
    def open_unit(profile: str | None) -> Iterator[Unit]:
        """One fresh unit per case, stopped on exit. ``warn`` keeps the run readable."""
        unit = create_unit(
            vendor=vendor, profile=profile or default_profile, sink=MemorySink(), logger=JsonLogger("warn")
        )
        try:
            yield unit
        finally:
            unit.stop()

    return open_unit


def square_target() -> FidelityTarget:
    """The first vendor with a fidelity declaration; see D-006."""
    return FidelityTarget(
        name=_SQUARE,
        anchor=_SQUARE_ANCHOR,
        open_unit=_opener(_SQUARE, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
    )


def lightspeed_target() -> FidelityTarget:
    """A vendored vendor: the specification is Apache 2.0, so the extract is
    committed and both fidelity steps run offline."""
    return FidelityTarget(
        name=_LIGHTSPEED,
        anchor=_LIGHTSPEED_ANCHOR,
        open_unit=_opener(_LIGHTSPEED, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
    )


def toast_target() -> FidelityTarget:
    """A non-vendored vendor: its extract is fetched, never committed, so the
    first use on a cold cache needs the network (konyklabs/roadmap#56)."""
    return FidelityTarget(
        name=_TOAST,
        anchor=_TOAST_ANCHOR,
        open_unit=_opener(_TOAST, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
    )
