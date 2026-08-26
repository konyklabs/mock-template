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
) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            profile=profile,
            vendor_config=vendor_config or {},
            chaos=SimpleNamespace(seed=chaos_seed),
        ),
        vendor=SimpleNamespace(name=vendor_name),
    )
