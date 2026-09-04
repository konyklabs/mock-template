"""Stubs for the two collaborators the vendor foundation reads but does not own.

A ``UnitContext`` is a protocol; the pieces under test here reach exactly three
things through it (the profile name, the vendor config block and the chaos
seed), so a namespace with those three is a truer test double than a whole unit
would be -- it fails if a projection or a shaper starts reading something else.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def fake_ctx(
    *,
    profile: str = "test",
    vendor_config: dict[str, Any] | None = None,
    chaos_seed: int = 1,
    vendor_name: str = "square",
    error_sidecar_mode: str = "both",
) -> Any:
    """``error_sidecar_mode`` defaults to ``"both"``, not the profile default
    of ``"headers"`` (konyklabs/roadmap#71): these shaper tests assert on the
    sidecar's *content*, a concern the wire placement does not change, and
    ``"both"`` keeps `shaped.body["unit_error"]` populated so that content is
    still readable as a plain dict rather than a JSON-encoded header value.
    """
    return SimpleNamespace(
        config=SimpleNamespace(
            profile=profile,
            vendor_config=vendor_config or {},
            chaos=SimpleNamespace(seed=chaos_seed),
            errors=SimpleNamespace(sidecar=error_sidecar_mode),
        ),
        vendor=SimpleNamespace(name=vendor_name),
    )


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    """One line: how many responses the Square suite validated against the spec."""
    from tests.unit.square.harness import LEDGER

    terminalreporter.write_line(f"square {LEDGER.summary()}")
