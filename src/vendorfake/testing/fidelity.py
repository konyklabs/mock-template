"""Fidelity targets for the vendors shipped in this distribution, usable from an
installed wheel with no checkout. ``vendorfake.fidelity`` may not import a vendor
or the registry, so the targets live here; ``tests/fidelity/harness.py``
re-exports them rather than defining its own.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from importlib import resources

from vendorfake import registry
from vendorfake.core.kernel.types import SignInput
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.fidelity.runner import FidelityTarget
from vendorfake.fidelity.types import Surface, load_declaration, load_extract
from vendorfake.registry import create_unit

__all__ = ["lightspeed_target", "square_target", "surface_for", "target_for", "toast_target"]

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


def _signer(vendor: str) -> Callable[[SignInput], Mapping[str, str]]:
    """The vendor's own webhook signer, for `vendorfake-fidelity webhooks`."""

    def sign(payload: SignInput) -> Mapping[str, str]:
        signer = registry.resolve_vendor(vendor).signer
        if signer is None:
            raise LookupError(f"{vendor} declares no webhook signer")
        return signer.sign(payload)

    return sign


def square_target() -> FidelityTarget:
    """The first vendor with a fidelity declaration; see D-006."""
    return FidelityTarget(
        name=_SQUARE,
        anchor=_SQUARE_ANCHOR,
        open_unit=_opener(_SQUARE, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
        signer=_signer(_SQUARE),
    )


def lightspeed_target() -> FidelityTarget:
    """A vendored vendor: the specification is Apache 2.0, so the extract is
    committed and both fidelity steps run offline."""
    return FidelityTarget(
        name=_LIGHTSPEED,
        anchor=_LIGHTSPEED_ANCHOR,
        open_unit=_opener(_LIGHTSPEED, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
        signer=_signer(_LIGHTSPEED),
    )


def toast_target() -> FidelityTarget:
    """A non-vendored vendor: its extract is fetched, never committed, so the
    first use on a cold cache needs the network (konyklabs/roadmap#56)."""
    return FidelityTarget(
        name=_TOAST,
        anchor=_TOAST_ANCHOR,
        open_unit=_opener(_TOAST, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
        signer=_signer(_TOAST),
    )


def target_for(vendor: str) -> FidelityTarget | None:
    """The target for a vendor name, or ``None`` when that vendor has no fidelity leg.
    Discovered from the tree: a leg is a ``vendorfake.<vendor>.fidelity`` package
    carrying ``declaration.json``, never a hand-kept list."""
    anchor = f"vendorfake.{vendor}.fidelity"
    if importlib.util.find_spec(anchor) is None or not resources.files(anchor).joinpath("declaration.json").is_file():
        return None
    return FidelityTarget(
        name=vendor,
        anchor=anchor,
        open_unit=_opener(vendor, _DEFAULT_PROFILE),
        default_profile=_DEFAULT_PROFILE,
        signer=_signer(vendor),
    )


def surface_for(target: FidelityTarget) -> Surface:
    """The target's declaration and extract, applied: what a validator checks against. May fetch, for a vendor
    whose specification is not vendored."""
    return Surface(load_declaration(target.anchor), load_extract(target.anchor))
