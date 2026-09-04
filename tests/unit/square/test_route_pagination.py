"""The pagination declarations C26 walks, pinned route by route.

The conformance walk covers whatever the route table declares, so the one
thing it cannot catch is a paginating route that declares nothing -- and the
one thing a reader cannot see from a green walk is which routes were excused.
Both are pinned here: every paginating Square route carries a PaginationSpec,
the walkable ones are named, and every opt-out carries a written reason.
"""

from __future__ import annotations

import json

from vendorfake.square.seed.constants import DEFAULT_SEED_PATH
from vendorfake.square.vendor import create_square_vendor

WALKABLE = {
    "SearchOrders",
    "ListCatalog",
    "SearchCatalogObjects",
    "SearchLoyaltyAccounts",
}

EXCUSED = {
    "ListMerchants",
    "RetrieveInventoryCount",
    "BatchRetrieveInventoryCounts",
}


def _declared() -> dict[str, object]:
    return {
        route.operation_id: route.pagination for route in create_square_vendor().routes if route.pagination is not None
    }


def test_every_paginating_route_declares_walkable_or_excused() -> None:
    declared = _declared()
    assert set(declared) == WALKABLE | EXCUSED, sorted(declared)
    for operation_id in WALKABLE:
        assert declared[operation_id].walkable, operation_id
    for operation_id in EXCUSED:
        spec = declared[operation_id]
        assert not spec.walkable, operation_id
        # The opt-out is a record, not a flag: an empty reason fails C26 too.
        assert spec.unwalkable_reason.strip(), operation_id


def test_search_orders_example_reaches_every_seeded_order() -> None:
    """The walk compares pages against the route's own listing, so the example
    body must reach every order the scenario seeds -- one location out of two
    published a one-row listing no page boundary can be forced across."""
    routes = {route.operation_id: route for route in create_square_vendor().routes}
    example = routes["SearchOrders"].example_body
    assert example is not None
    seed = json.loads(DEFAULT_SEED_PATH.read_text())
    seeded = {order["location_id"] for order in seed["orders"]}
    assert seeded <= set(example["location_ids"]), (seeded, example)
