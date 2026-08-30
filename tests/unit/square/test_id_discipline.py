"""A rejected request draws nothing from the id stream.

Two units, seeded alike, mint the same ids for the same accepted traffic. A
request that is refused must not disturb that: the id a later accepted
request mints has to be the one it would have minted had the refusal never
happened. PayOrder keeps this for tender ids by minting inside the mutator;
the surfaces below keep it by validating everything before minting anything.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.unit.square.harness import Harness
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import SEED_LOCATION_ID, SEED_OPEN_ORDER_ID, TEA_ITEM_ID, TEA_MUG_VARIATION_ID

Ids = Callable[[Harness], list[str]]
Reject = Callable[[Harness], None]


def upsert_ids(h: Harness) -> list[str]:
    response = h.api.post(
        "/v2/catalog/object",
        {
            "idempotency_key": "ok",
            "object": {
                "type": "ITEM",
                "id": "#Scone",
                "item_data": {
                    "name": "Scone",
                    "variations": [
                        {
                            "type": "ITEM_VARIATION",
                            "id": "#Plain",
                            "item_variation_data": {"name": "Plain", "price_money": {"amount": 300}},
                        }
                    ],
                },
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return [row["object_id"] for row in response.json()["id_mappings"]]


def upsert_rejected(h: Harness) -> None:
    """Two temporary ids are classified before the missing name is found."""
    response = h.api.post(
        "/v2/catalog/object",
        {
            "idempotency_key": "bad",
            "object": {
                "type": "ITEM",
                "id": "#A",
                "item_data": {
                    "name": "A",
                    "variations": [
                        {"type": "ITEM_VARIATION", "id": "#B", "item_variation_data": {"price_money": {"amount": 1}}}
                    ],
                },
            },
        },
        headers=h.auth,
    )
    assert response.status == 400, response.text
    assert response.json()["errors"][0]["field"] == "object.item_data.variations[0].item_variation_data.name"


def inventory_ids(h: Harness) -> list[str]:
    response = h.api.post(
        "/v2/inventory/changes/batch-create",
        {
            "idempotency_key": "ok",
            "changes": [
                {
                    "type": "ADJUSTMENT",
                    "adjustment": {
                        "catalog_object_id": TEA_MUG_VARIATION_ID,
                        "location_id": SEED_LOCATION_ID,
                        "quantity": "1",
                        "from_state": "NONE",
                        "to_state": "IN_STOCK",
                    },
                }
            ],
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return [row["adjustment"]["id"] for row in response.json()["changes"]]


def inventory_rejected(h: Harness) -> None:
    """The first change is valid; the second names an ITEM, not a variation."""
    good = {
        "type": "PHYSICAL_COUNT",
        "physical_count": {"catalog_object_id": TEA_MUG_VARIATION_ID, "location_id": SEED_LOCATION_ID, "quantity": "9"},
    }
    bad = {
        "type": "PHYSICAL_COUNT",
        "physical_count": {"catalog_object_id": TEA_ITEM_ID, "location_id": SEED_LOCATION_ID, "quantity": "9"},
    }
    response = h.api.post(
        "/v2/inventory/changes/batch-create", {"idempotency_key": "bad", "changes": [good, bad]}, headers=h.auth
    )
    assert response.status == 400, response.text


def fulfillment_ids(h: Harness) -> list[str]:
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": "ok",
            "order": {"location_id": SEED_LOCATION_ID, "fulfillments": [{"type": "PICKUP"}]},
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return [f["uid"] for f in response.json()["order"]["fulfillments"]]


def fulfillment_rejected(h: Harness) -> None:
    """The first fulfillment is fine; the second has no type. This also
    covers the order id itself: `_create` validates before minting it."""
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": "bad",
            "order": {"location_id": SEED_LOCATION_ID, "fulfillments": [{"type": "PICKUP"}, {"uid": "x"}]},
        },
        headers=h.auth,
    )
    assert response.status == 400, response.text


def line_item_rejected(h: Harness) -> None:
    """A bad line item: the order id must not have been minted either."""
    response = h.api.post(
        "/v2/orders",
        {"idempotency_key": "bad-line", "order": {"location_id": SEED_LOCATION_ID, "line_items": [{"quantity": "1"}]}},
        headers=h.auth,
    )
    assert response.status == 400, response.text


def fulfillment_update_ids(h: Harness) -> list[str]:
    created = h.api.post(
        "/v2/orders", {"idempotency_key": "base", "order": {"location_id": SEED_LOCATION_ID}}, headers=h.auth
    ).json()["order"]
    response = h.api.put(
        f"/v2/orders/{created['id']}",
        {"idempotency_key": "ok", "order": {"version": 1, "fulfillments": [{"type": "DELIVERY"}]}},
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return [f["uid"] for f in response.json()["order"]["fulfillments"]]


def fulfillment_update_rejected(h: Harness) -> None:
    """Against the seeded order, so the refusal is the only thing this path
    adds over the clean one."""
    response = h.api.put(
        f"/v2/orders/{SEED_OPEN_ORDER_ID}",
        {"idempotency_key": "bad", "order": {"version": 1, "fulfillments": [{"type": "DELIVERY"}, {"type": "DRONE"}]}},
        headers=h.auth,
    )
    assert response.status == 400, response.text


def fulfillment_update_stale_version(h: Harness) -> None:
    """Move the seeded order to version 2, then PUT with version 1: the stale
    write must be refused before any uid is minted for the new entry."""
    moved = h.api.put(
        f"/v2/orders/{SEED_OPEN_ORDER_ID}",
        {"idempotency_key": "move", "order": {"version": 1, "ticket_name": "Bar"}},
        headers=h.auth,
    )
    assert moved.status == 200, moved.text
    stale = h.api.put(
        f"/v2/orders/{SEED_OPEN_ORDER_ID}",
        {"idempotency_key": "stale", "order": {"version": 1, "fulfillments": [{"type": "DELIVERY"}]}},
        headers=h.auth,
    )
    assert stale.status == 400, stale.text
    assert stale.json()["errors"][0]["code"] == "VERSION_MISMATCH"


def minted(ids: Ids, *, after: Reject | None) -> list[str]:
    for h in build_harness("full"):
        if after is not None:
            after(h)
        return ids(h)
    raise AssertionError("harness yielded nothing")


@pytest.mark.parametrize(
    ("ids", "reject"),
    [
        pytest.param(upsert_ids, upsert_rejected, id="catalog-upsert"),
        pytest.param(inventory_ids, inventory_rejected, id="inventory-batch"),
        pytest.param(fulfillment_ids, fulfillment_rejected, id="order-fulfillments"),
        pytest.param(fulfillment_ids, line_item_rejected, id="order-line-items"),
        pytest.param(fulfillment_update_ids, fulfillment_update_rejected, id="update-fulfillments"),
        pytest.param(fulfillment_update_ids, fulfillment_update_stale_version, id="update-fulfillments-stale-version"),
    ],
)
def test_a_rejected_request_leaves_the_next_ids_unchanged(ids: Ids, reject: Reject) -> None:
    clean: list[Any] = minted(ids, after=None)
    assert clean
    assert minted(ids, after=reject) == clean
