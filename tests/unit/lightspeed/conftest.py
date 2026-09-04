"""Fixtures shared by the Lightspeed suite.

``fake_ctx`` is the context stub the error shaper and the rate limiter are
tested against directly, without building a unit: four things reach them
through ``UnitContext`` -- the profile name, the vendor config block, the
sidecar mode and the clock.

``h`` is a started unit on the ``full`` profile; ``profile_harness`` builds one
on any other.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.lightspeed.harness import Harness, harness
from vendorfake.core.time.clock import Clock

CLOCK_START = "2026-09-04T12:00:00.000Z"


def fake_ctx(
    *,
    profile: str = "test",
    vendor_config: dict[str, Any] | None = None,
    chaos_seed: int = 1,
    vendor_name: str = "lightspeed",
    clock: Clock | None = None,
    error_sidecar_mode: str = "both",
) -> Any:
    """``error_sidecar_mode`` defaults to ``"both"``, not the profile default
    of ``"headers"``: these shaper tests assert on the sidecar's *content*, a
    concern the wire placement does not change."""
    return SimpleNamespace(
        config=SimpleNamespace(
            profile=profile,
            vendor_config=vendor_config or {},
            chaos=SimpleNamespace(seed=chaos_seed),
            errors=SimpleNamespace(sidecar=error_sidecar_mode),
        ),
        vendor=SimpleNamespace(name=vendor_name),
        clock=clock if clock is not None else Clock("virtual", CLOCK_START),
    )


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


@pytest.fixture
def virtual() -> Iterator[Harness]:
    """A unit on a virtual clock, so a test can advance time deliberately."""
    yield from harness(env={"VENDORFAKE_CLOCK": "virtual", "VENDORFAKE_CLOCK_START": CLOCK_START})
