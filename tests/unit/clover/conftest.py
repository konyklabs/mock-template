"""The same three-field context stub the square suite uses.

The pieces under test reach exactly three things through the ``UnitContext``
protocol -- the profile name, the vendor config block and the chaos seed -- so
a namespace with those three is a truer test double than a whole unit.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def fake_ctx(
    *,
    profile: str = "test",
    vendor_config: dict[str, Any] | None = None,
    chaos_seed: int = 1,
    vendor_name: str = "clover",
) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            profile=profile,
            vendor_config=vendor_config or {},
            chaos=SimpleNamespace(seed=chaos_seed),
        ),
        vendor=SimpleNamespace(name=vendor_name),
    )
