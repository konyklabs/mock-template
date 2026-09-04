"""The context stub the foundation pieces are tested against.

Four things reach the shaper and the vendor through ``UnitContext``: the
profile name, the vendor config block, the chaos seed, and -- for the 429
headers' ``X-Toast-RateLimit-Reset`` -- the clock.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from vendorfake.core.time.clock import Clock


def fake_ctx(
    *,
    profile: str = "test",
    vendor_config: dict[str, Any] | None = None,
    chaos_seed: int = 1,
    vendor_name: str = "toast",
    clock: Clock | None = None,
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
        clock=clock if clock is not None else Clock("virtual", "2026-08-30T12:00:00.000Z"),
    )
