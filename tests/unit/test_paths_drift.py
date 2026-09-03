"""``<vendor>/paths.py`` constants cannot drift from the router.

FOR: the conformance-style pin the spec asks for -- one hand-written constant
per non-internal route carrying an ``operation_id``, its value equal to that
route's path, and no constant naming a route that does not exist. Read from
each vendor's *own* route table (built through its public ``create_*_vendor``
factory, never imported from ``paths.py`` itself), so a hand-edited constant
that has quietly stopped matching the router is exactly what this file is
for -- not a copy of the same literal checked against itself.

The UPPER_SNAKE conversion is duplicated from the one-off script that
generated the three ``paths.py`` files rather than imported from anywhere:
there is nowhere in the shipped distribution it belongs (it is not vendor
logic and it is not core logic), and keeping it here, next to the assertion
that depends on it, is what makes this test able to fail on its own if the
convention and the files it checks ever disagree.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from vendorfake.clover import paths as clover_paths
from vendorfake.clover.vendor import create_clover_vendor
from vendorfake.core.kernel.types import VendorDefinition
from vendorfake.square import paths as square_paths
from vendorfake.square.vendor import create_square_vendor
from vendorfake.toast import paths as toast_paths
from vendorfake.toast.vendor import create_toast_vendor

VENDORS: tuple[tuple[str, Callable[[], VendorDefinition], object], ...] = (
    ("square", create_square_vendor, square_paths),
    ("clover", create_clover_vendor, clover_paths),
    ("toast", create_toast_vendor, toast_paths),
)


def _upper_snake(operation_id: str) -> str:
    """``ObtainToken`` -> ``OBTAIN_TOKEN``; ``MenusV3Get`` -> ``MENUS_V3_GET``.

    The same three-pass boundary insertion an ordinary camelCase-to-snake_case
    converter uses, run once more before upper-casing so a run of capitals
    (``V3``) does not get a boundary inserted before every letter in it.
    """
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", operation_id)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    s3 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s2)
    return s3.upper()


@pytest.mark.parametrize("name,factory,module", VENDORS, ids=[row[0] for row in VENDORS])
def test_every_operation_id_has_exactly_one_constant_and_the_values_agree(
    name: str, factory: Callable[[], VendorDefinition], module: object
) -> None:
    vendor = factory()
    expected: dict[str, str] = {}
    for route in vendor.routes:
        if route.internal or route.operation_id is None:
            continue
        const = _upper_snake(route.operation_id)
        assert const not in expected or expected[const] == route.path, (
            f"{name}: operation_id {route.operation_id!r} collides with an existing constant {const!r} "
            f"under a different path -- the UPPER_SNAKE convention is no longer collision-free for this "
            f"vendor's operation ids."
        )
        expected[const] = route.path

    names: tuple[str, ...] = module.__all__  # type: ignore[attr-defined]
    published = {const: getattr(module, const) for const in names}

    missing = sorted(set(expected) - set(published))
    assert not missing, (
        f"{name}/paths.py is missing a constant for {missing} -- every non-internal route with an "
        f"operation_id needs one (see the module's DoD in C-discovery.md)."
    )
    orphaned = sorted(set(published) - set(expected))
    assert not orphaned, (
        f"{name}/paths.py declares {orphaned}, which names no current non-internal route with that "
        f"operation_id -- a route was renamed or removed and the constant was not."
    )
    disagreements = [
        f"{const}: paths.py says {published[const]!r}, the router says {expected[const]!r}"
        for const in expected
        if published[const] != expected[const]
    ]
    assert not disagreements, f"{name}/paths.py has drifted from the router:\n" + "\n".join(disagreements)
