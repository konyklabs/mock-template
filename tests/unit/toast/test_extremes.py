"""Caller-supplied extremes answer the documented 400, never a 500.

konyklabs/roadmap#41: three values a caller may legitimately send reached an
unguarded numeric or calendar operation, the exception escaped, and the unit
answered a 500 carrying the raw Python message. The fixes live at the three
conversion funnels (``money.to_cents``, ``pricing._round``,
``dates.business_date``); this module is the walk that keeps the class closed.
Driven here: every numeric leaf of the order example body and every payment
amount, plus every caller instant, with every extreme, across ``/prices``,
``/orders``, payment POST and tip PATCH -- none may answer 5xx. Routes not
walked (the standalone selections and discount writes, the ``/ordersBulk``
query dates) reach the same three funnels, which is what the funnel guards --
not this walk -- keep closed.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.toast.harness import Harness, harness
from tests.unit.toast.test_surface_orders import order_body
from tests.unit.toast.test_surface_payments import OTHER
from vendorfake.toast.model.dates import business_date

EXTREME_NUMBERS: tuple[Any, ...] = (1e308, -1e308, "1e999", "-1e999", 1e300, 9.9e27)
"""Finite floats past the Decimal context's 28 significant digits, their string
spellings, and one just under the edge that overflows only when multiplied."""

EXTREME_INSTANTS: tuple[str, ...] = (
    "0001-01-01T00:00:00.000+0000",
    "0001-01-01T03:59:59.000+0000",
    "9999-12-31T23:59:59.999+0000",
)
"""The regex-legal edges of the calendar: the first two underflow the zone
shift or the closeout subtraction, the last must simply work."""


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def _numeric_paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Every path in ``node`` whose leaf is a caller number (bools excluded)."""
    found: list[tuple[Any, ...]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_numeric_paths(value, (*prefix, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_numeric_paths(value, (*prefix, index)))
    elif isinstance(node, int | float) and not isinstance(node, bool):
        found.append(prefix)
    return found


def _with(body: dict[str, Any], path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    mutated = copy.deepcopy(body)
    node: Any = mutated
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return mutated


def _no_5xx(h: Harness, method: str, route: str, body: Any, label: str) -> None:
    response = getattr(h, method)(route, body)
    assert response.status < 500, f"{label} -> {response.status} {response.text[:200]}"


def test_every_caller_number_on_the_order_routes_survives_every_extreme(h: Harness) -> None:
    base = order_body(
        {
            "item": order_body()["checks"][0]["selections"][0]["item"],
            "quantity": 2.0,
            "openPriceAmount": 1.0,
            "modifiers": [],
        }
    )
    paths = _numeric_paths(base)
    assert paths, f"the walk found no numeric leaf in {base}"
    for route in ("/orders/v2/prices", "/orders/v2/orders"):
        for path in paths:
            for value in EXTREME_NUMBERS:
                label = f"POST {route} {'.'.join(str(p) for p in path)}={value!r}"
                _no_5xx(h, "post", route, _with(base, path, value), label)


def test_every_caller_instant_on_the_order_routes_survives_the_calendar_edges(h: Harness) -> None:
    for route in ("/orders/v2/prices", "/orders/v2/orders"):
        for field in ("openedDate", "promisedDate"):
            for instant in EXTREME_INSTANTS:
                body = order_body()
                body[field] = instant
                _no_5xx(h, "post", route, body, f"POST {route} {field}={instant}")


def test_payment_numbers_and_instants_survive_every_extreme(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check = order["guid"], order["checks"][0]["guid"]
    route = f"/orders/v2/orders/{guid}/checks/{check}/payments"
    for field in ("amount", "tipAmount", "amountTendered"):
        for value in EXTREME_NUMBERS:
            _no_5xx(h, "post", route, [{**OTHER, field: value}], f"POST payments {field}={value!r}")
    for instant in EXTREME_INSTANTS:
        _no_5xx(h, "post", route, [{**OTHER, "paidDate": instant}], f"POST payments paidDate={instant}")
    # A fresh order for the PATCH block: the year-9999 paidDate above is
    # legitimate and PAYS the first check.
    fresh = h.post("/orders/v2/orders", order_body()).json()
    route = f"/orders/v2/orders/{fresh['guid']}/checks/{fresh['checks'][0]['guid']}/payments"
    paid = h.post(route, [OTHER])
    assert paid.status == 200, f"{paid.status} {paid.text[:200]}"
    payment = paid.json()["checks"][0]["payments"][0]["guid"]
    for value in EXTREME_NUMBERS:
        _no_5xx(
            h,
            "patch",
            f"{route}/{payment}",
            {"tipAmount": value},
            f"PATCH payment tipAmount={value!r}",
        )


def test_the_three_filed_instances_answer_400_naming_the_field(h: Harness) -> None:
    cases = [
        ({"selection": {"openPriceAmount": 1e308}}, "checks[0].selections[0].openPriceAmount"),
        ({"selection": {"openPriceAmount": "1e999"}}, "checks[0].selections[0].openPriceAmount"),
        ({"selection": {"quantity": 1e300}}, "checks[0].selections[0].quantity"),
        ({"order": {"openedDate": "0001-01-01T00:00:00.000+0000"}}, "openedDate"),
    ]
    for overrides, named in cases:
        # Deep-copied because order_body() aliases the module-level SOUP dict,
        # and an in-place update would leak one case's field into the next.
        body = copy.deepcopy(order_body())
        body["checks"][0]["selections"][0].update(overrides.get("selection", {}))
        body.update(overrides.get("order", {}))
        response = h.post("/orders/v2/prices", body)
        document = response.json()
        assert response.status == 400, f"{named}: {response.status} {response.text[:200]}"
        assert response.headers.get("x-unit-error") == "invalid_value", response.headers.get("x-unit-error")
        assert named in document["message"], f"{named!r} not named in {document['message']!r}"


def test_a_seed_or_clock_driven_instant_still_crashes_rather_than_hiding(h: Harness) -> None:
    with pytest.raises(OverflowError):
        business_date(-9e15, time_zone="America/New_York", closeout_hour=4)
