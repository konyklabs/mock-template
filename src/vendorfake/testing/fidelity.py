"""Fidelity targets for the vendors shipped in this distribution.

FOR: a consumer who installed the wheel and wants to run the documented corpus
and the per-route matrix without a checkout::

    python -m vendorfake.fidelity report --target vendorfake.testing.fidelity:square_target
    pytest --pyargs vendorfake.fidelity -p vendorfake.fidelity.plugin --fidelity-target vendorfake.testing.fidelity:square_target

``vendorfake.fidelity`` may not import a vendor or the registry, so the
targets live here, one layer out, exactly as the conformance targets do in
``vendorfake.testing.conformance``. The repository's own harness
(``tests/fidelity/harness.py``) re-exports these rather than defining its own,
so the wheel's target and CI's cannot disagree.
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
    """The first VENDORED vendor after Square: the specification is published
    under Apache 2.0, so a structural extract may be committed and no ``fetch``
    step is needed.

    Both fidelity steps therefore run offline for this vendor: there is no
    ``fetch`` to pay for, and ``pin --check --offline`` compares a committed
    ``extract.json`` against a committed ``pin.json``."""
    return FidelityTarget(
        name=_LIGHTSPEED,
        anchor=_LIGHTSPEED_ANCHOR,
        open_unit=_opener(_LIGHTSPEED, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
    )


def toast_target() -> FidelityTarget:
    """The first non-vendored vendor: its extract is fetched, never committed
    (konyklabs/roadmap#56), so the first use on a cold cache needs the network
    -- ``vendorfake-fidelity fetch --target vendorfake.testing.fidelity:toast_target``
    is the step that pays for it once."""
    return FidelityTarget(
        name=_TOAST,
        anchor=_TOAST_ANCHOR,
        open_unit=_opener(_TOAST, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
    )
