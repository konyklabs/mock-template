"""The Inventory surface: physical counts and adjustments to IN_STOCK, the
two reads, and the events the catalog and inventory writes fire.

https://developer.squareup.com/reference/square/inventory-api
https://developer.squareup.com/reference/square/webhooks/inventory.count.updated
https://developer.squareup.com/reference/square/webhooks/catalog.version.updated
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.square.seed.constants import (
    COLD_BREW_SMALL_VARIATION_ID,
    SEED_INVENTORY_CALCULATED_AT,
    SEED_KIOSK_LOCATION_ID,
    SEED_LOCATION_ID,
    SEED_MERCHANT_ID,
    TEA_ITEM_ID,
    TEA_MUG_VARIATION_ID,
    TEA_POT_VARIATION_ID,
)


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("full", env={"VENDORFAKE_CLOCK": "virtual"})


def change(h: Harness, changes: list[dict[str, Any]], key: str = "inv-1", **body: Any) -> Any:
    return h.api.post(
        "/v2/inventory/changes/batch-create", {"idempotency_key": key, "changes": changes, **body}, headers=h.auth
    )


def physical(object_id: str, quantity: str, location_id: str = SEED_LOCATION_ID, **extra: Any) -> dict[str, Any]:
    return {
        "type": "PHYSICAL_COUNT",
        "physical_count": {"catalog_object_id": object_id, "location_id": location_id, "quantity": quantity, **extra},
    }


def adjustment(object_id: str, quantity: str, from_state: str, to_state: str) -> dict[str, Any]:
    return {
        "type": "ADJUSTMENT",
        "adjustment": {
            "catalog_object_id": object_id,
            "location_id": SEED_LOCATION_ID,
            "quantity": quantity,
            "from_state": from_state,
            "to_state": to_state,
        },
    }


def retrieve(h: Harness, **body: Any) -> Any:
    return h.api.post("/v2/inventory/counts/batch-retrieve", body, headers=h.auth)


def quantity_of(h: Harness, object_id: str, location_id: str = SEED_LOCATION_ID) -> str | None:
    counts = retrieve(h, catalog_object_ids=[object_id], location_ids=[location_id]).json()["counts"]
    return None if not counts else str(counts[0]["quantity"])


def journal_seq(h: Harness) -> int:
    return int(h.api.get("/__unit/journal").json()["seq"])


# ---------------------------------------------------------------------------
# Reads over the seed
# ---------------------------------------------------------------------------


def test_batch_retrieve_returns_the_seeded_counts_in_the_documented_shape(h: Harness) -> None:
    body = retrieve(h).json()
    assert "cursor" not in body
    by_object = {row["catalog_object_id"]: row for row in body["counts"]}
    assert set(by_object) == {TEA_MUG_VARIATION_ID, COLD_BREW_SMALL_VARIATION_ID}
    mug = by_object[TEA_MUG_VARIATION_ID]
    assert list(mug) == [
        "catalog_object_id",
        "catalog_object_type",
        "state",
        "location_id",
        "quantity",
        "calculated_at",
    ]
    assert mug["catalog_object_type"] == "ITEM_VARIATION"
    assert mug["state"] == "IN_STOCK"
    assert mug["location_id"] == SEED_LOCATION_ID
    assert mug["quantity"] == "25"
    assert mug["calculated_at"] == SEED_INVENTORY_CALCULATED_AT


def test_batch_retrieve_filters_by_object_location_state_and_time(h: Harness) -> None:
    assert [
        r["catalog_object_id"] for r in retrieve(h, catalog_object_ids=[TEA_MUG_VARIATION_ID]).json()["counts"]
    ] == [TEA_MUG_VARIATION_ID]
    assert retrieve(h, location_ids=[SEED_KIOSK_LOCATION_ID]).json()["counts"] == []
    assert retrieve(h, states=["SOLD"]).json()["counts"] == []
    assert len(retrieve(h, states=["in_stock"]).json()["counts"]) == 2
    assert retrieve(h, updated_after=SEED_INVENTORY_CALCULATED_AT).json()["counts"] == []
    assert len(retrieve(h, updated_after="2026-01-01T00:00:00Z").json()["counts"]) == 2


def test_batch_retrieve_pages_and_refuses_a_zero_limit(h: Harness) -> None:
    first = retrieve(h, limit=1).json()
    assert len(first["counts"]) == 1
    assert first["cursor"]
    rest = retrieve(h, limit=1, cursor=first["cursor"]).json()
    assert len(rest["counts"]) == 1
    assert "cursor" not in rest
    assert first_error(retrieve(h, limit=0))["field"] == "limit"


def test_retrieve_count_reads_one_variation_across_locations(h: Harness) -> None:
    """ "location_ids: The Location IDs to look up as a comma-separated list." """
    everywhere = h.api.get(f"/v2/inventory/{TEA_MUG_VARIATION_ID}", headers=h.auth).json()
    assert [r["quantity"] for r in everywhere["counts"]] == ["25"]
    kiosk = h.api.call(
        method="GET",
        path=f"/v2/inventory/{TEA_MUG_VARIATION_ID}",
        query={"location_ids": SEED_KIOSK_LOCATION_ID},
        headers=h.auth,
    ).json()
    assert kiosk == {"counts": []}
    never_counted = h.api.get(f"/v2/inventory/{TEA_POT_VARIATION_ID}", headers=h.auth).json()
    assert never_counted == {"counts": []}


# ---------------------------------------------------------------------------
# BatchChangeInventory
# ---------------------------------------------------------------------------


def test_a_physical_count_sets_the_quantity_and_echoes_the_change(h: Harness) -> None:
    seq = journal_seq(h)
    response = change(h, [physical(TEA_MUG_VARIATION_ID, "40", reference_id="stocktake-1")])
    assert response.status == 200, response.text
    body = response.json()
    (count,) = body["counts"]
    assert count["quantity"] == "40"
    assert count["calculated_at"] == h.unit.context.clock.iso_ms()
    (echo,) = body["changes"]
    assert echo["type"] == "PHYSICAL_COUNT"
    pc = echo["physical_count"]
    assert len(pc["id"]) == 24
    assert pc["reference_id"] == "stocktake-1"
    assert pc["state"] == "IN_STOCK"
    assert pc["catalog_object_type"] == "ITEM_VARIATION"
    assert pc["quantity"] == "40"
    assert pc["occurred_at"] == pc["created_at"]
    assert quantity_of(h, TEA_MUG_VARIATION_ID) == "40"
    assert journal_seq(h) == seq + 1


def test_a_physical_count_of_an_uncounted_variation_creates_its_count(h: Harness) -> None:
    assert quantity_of(h, TEA_POT_VARIATION_ID) is None
    assert change(h, [physical(TEA_POT_VARIATION_ID, "12")]).status == 200
    assert quantity_of(h, TEA_POT_VARIATION_ID) == "12"


def test_adjustments_move_quantity_into_and_out_of_in_stock(h: Harness) -> None:
    """Receiving stock adds; selling or wasting it subtracts; a count may go
    negative, as Square's can."""
    response = change(
        h,
        [
            adjustment(TEA_MUG_VARIATION_ID, "5", "RECEIVED_FROM_VENDOR", "IN_STOCK"),
            adjustment(TEA_MUG_VARIATION_ID, "2", "IN_STOCK", "SOLD"),
            adjustment(COLD_BREW_SMALL_VARIATION_ID, "10", "IN_STOCK", "WASTE"),
        ],
    )
    assert response.status == 200, response.text
    by_object = {row["catalog_object_id"]: row["quantity"] for row in response.json()["counts"]}
    assert by_object == {TEA_MUG_VARIATION_ID: "28", COLD_BREW_SMALL_VARIATION_ID: "-2"}
    assert quantity_of(h, TEA_MUG_VARIATION_ID) == "28"
    assert quantity_of(h, COLD_BREW_SMALL_VARIATION_ID) == "-2"
    (first, second, third) = response.json()["changes"]
    assert first["adjustment"]["from_state"] == "RECEIVED_FROM_VENDOR"
    assert second["adjustment"]["to_state"] == "SOLD"
    assert third["adjustment"]["quantity"] == "10"


def test_decimal_quantities_are_kept_and_normalised(h: Harness) -> None:
    """ "a decimal string ... up to 5 digits after the decimal point." """
    assert change(h, [physical(TEA_MUG_VARIATION_ID, "1.50")]).json()["counts"][0]["quantity"] == "1.5"
    assert (
        change(h, [adjustment(TEA_MUG_VARIATION_ID, "0.25", "NONE", "IN_STOCK")], key="inv-2").json()["counts"][0][
            "quantity"
        ]
        == "1.75"
    )
    too_fine = change(h, [physical(TEA_MUG_VARIATION_ID, "1.123456")], key="inv-3")
    assert first_error(too_fine)["field"] == "changes[0].physical_count.quantity"
    junk = change(h, [physical(TEA_MUG_VARIATION_ID, "lots")], key="inv-4")
    assert first_error(junk)["field"] == "changes[0].physical_count.quantity"
    number = h.api.post(
        "/v2/inventory/changes/batch-create",
        {
            "idempotency_key": "inv-5",
            "changes": [
                {
                    "type": "PHYSICAL_COUNT",
                    "physical_count": {
                        "catalog_object_id": TEA_MUG_VARIATION_ID,
                        "location_id": SEED_LOCATION_ID,
                        "quantity": 5,
                    },
                }
            ],
        },
        headers=h.auth,
    )
    assert first_error(number)["field"] == "changes[0].physical_count.quantity"


def test_an_unchanged_physical_count_is_ignored_by_default(h: Harness) -> None:
    """ "ignore_unchanged_counts ... Default: true" -- nothing written,
    nothing journalled, and the change still echoed."""
    seq = journal_seq(h)
    response = change(h, [physical(TEA_MUG_VARIATION_ID, "25")])
    assert response.status == 200, response.text
    assert len(response.json()["changes"]) == 1
    assert response.json()["counts"][0]["calculated_at"] == SEED_INVENTORY_CALCULATED_AT
    assert journal_seq(h) == seq

    honoured = change(h, [physical(TEA_MUG_VARIATION_ID, "25")], key="inv-2", ignore_unchanged_counts=False)
    assert honoured.status == 200
    assert journal_seq(h) == seq + 1


def test_a_bad_change_anywhere_in_the_batch_writes_nothing(h: Harness) -> None:
    """The invariant: the batch is validated whole before its first write."""
    seq = journal_seq(h)
    response = change(
        h,
        [physical(TEA_MUG_VARIATION_ID, "40"), physical(TEA_ITEM_ID, "1")],
    )
    assert response.status == 400
    assert first_error(response)["field"] == "changes[1].physical_count.catalog_object_id"
    assert journal_seq(h) == seq
    assert quantity_of(h, TEA_MUG_VARIATION_ID) == "25"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"type": "TRANSFER"}, "changes[0].type"),
        ({"type": "RESTOCK"}, "changes[0].type"),
        ({"type": "PHYSICAL_COUNT"}, "changes[0].physical_count"),
        (physical(TEA_MUG_VARIATION_ID, "1", state="SOLD"), "changes[0].physical_count.state"),
        (physical(TEA_MUG_VARIATION_ID, "1", location_id="NOSUCH"), "changes[0].physical_count.location_id"),
        (adjustment(TEA_MUG_VARIATION_ID, "1", "SOLD", "WASTE"), "changes[0].adjustment.to_state"),
        (adjustment(TEA_MUG_VARIATION_ID, "1", "PIGEON", "IN_STOCK"), "changes[0].adjustment.from_state"),
        (adjustment(TEA_MUG_VARIATION_ID, "-1", "NONE", "IN_STOCK"), "changes[0].adjustment.quantity"),
    ],
)
def test_a_change_this_unit_cannot_apply_is_refused_naming_the_field(
    h: Harness, body: dict[str, Any], field: str
) -> None:
    response = change(h, [body])
    assert response.status == 400
    assert first_error(response)["field"] == field


def test_batch_create_requires_an_idempotency_key_and_replays(h: Harness) -> None:
    missing = h.api.post("/v2/inventory/changes/batch-create", {"changes": []}, headers=h.auth)
    assert first_error(missing)["field"] == "idempotency_key"
    first = change(h, [adjustment(TEA_MUG_VARIATION_ID, "1", "NONE", "IN_STOCK")], key="replay")
    again = change(h, [adjustment(TEA_MUG_VARIATION_ID, "1", "NONE", "IN_STOCK")], key="replay")
    assert first.json() == again.json()
    assert quantity_of(h, TEA_MUG_VARIATION_ID) == "26"


def test_scopes_are_inventory_read_and_write(h: Harness) -> None:
    """https://developer.squareup.com/docs/oauth-api/square-permissions"""
    assert retrieve(h).status == 200
    assert h.api.post("/v2/inventory/counts/batch-retrieve", {}, headers=h.read_auth).status == 200
    refused = h.api.post(
        "/v2/inventory/changes/batch-create",
        {"idempotency_key": "ro", "changes": [physical(TEA_MUG_VARIATION_ID, "1")]},
        headers=h.read_auth,
    )
    assert refused.status == 403


def test_the_surface_is_its_own_capability() -> None:
    for scoped in build_harness("orders-only"):
        response = scoped.api.post("/v2/inventory/counts/batch-retrieve", {}, headers=scoped.auth)
        assert response.status == 501
        assert response.headers["x-unit-capability"] == "inventory"


# ---------------------------------------------------------------------------
# The two events
# ---------------------------------------------------------------------------


def subscribe(h: Harness, sink: MemorySink, *types: str) -> None:
    del sink
    response = h.api.post(
        "/__unit/webhooks/subscriptions",
        {"notification_url": "https://example.test/hooks", "event_types": list(types), "signature_key": "k"},
    )
    assert response.status == 201


def delivered(h: Harness, sink: MemorySink) -> list[dict[str, Any]]:
    h.api.post("/__unit/webhooks/drain", {})
    return [json.loads(bytes(r.body).decode("utf-8")) for r in sink.received]


def test_inventory_count_updated_carries_the_count_as_an_array() -> None:
    """https://developer.squareup.com/reference/square/webhooks/inventory.count.updated
    -- `data.type` is `inventory` and `data.object.inventory_counts` holds
    the changed count, in the documented field order."""
    sink = MemorySink()
    for h in build_harness("full", sink=sink, env={"VENDORFAKE_CLOCK": "virtual"}):
        subscribe(h, sink, "inventory.count.updated")
        change(h, [physical(TEA_MUG_VARIATION_ID, "40"), physical(TEA_POT_VARIATION_ID, "3")])
        bodies = delivered(h, sink)
        assert [b["type"] for b in bodies] == ["inventory.count.updated", "inventory.count.updated"]
        body = bodies[0]
        assert body["merchant_id"] == SEED_MERCHANT_ID
        assert body["data"]["type"] == "inventory"
        assert body["data"]["id"] == f"{TEA_MUG_VARIATION_ID}:{SEED_LOCATION_ID}"
        (count,) = body["data"]["object"]["inventory_counts"]
        assert list(count) == [
            "calculated_at",
            "catalog_object_id",
            "catalog_object_type",
            "location_id",
            "quantity",
            "state",
        ]
        assert count["quantity"] == "40"
        assert count["state"] == "IN_STOCK"
        assert bodies[1]["data"]["object"]["inventory_counts"][0]["quantity"] == "3"


def test_catalog_version_updated_fires_per_written_object() -> None:
    """https://developer.squareup.com/reference/square/webhooks/catalog.version.updated
    -- `data.type` is `catalog` and the object is `{catalog_version: {updated_at}}`.
    One event per committed object (JUDGMENT, stated in the mapper): an item
    with one variation is two."""
    sink = MemorySink()
    for h in build_harness("full", sink=sink, env={"VENDORFAKE_CLOCK": "virtual"}):
        subscribe(h, sink, "catalog.version.updated")
        response = h.api.post(
            "/v2/catalog/object",
            {
                "idempotency_key": "cat-1",
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
        item = response.json()["catalog_object"]
        bodies = delivered(h, sink)
        assert [b["type"] for b in bodies] == ["catalog.version.updated", "catalog.version.updated"]
        assert [b["data"]["id"] for b in bodies] == [item["id"], item["item_data"]["variations"][0]["id"]]
        for body in bodies:
            assert body["data"]["type"] == "catalog"
            assert body["data"]["object"] == {"catalog_version": {"updated_at": item["updated_at"]}}


def test_a_rejected_batch_and_a_seeded_count_fire_nothing() -> None:
    sink = MemorySink()
    for h in build_harness("full", sink=sink):
        subscribe(h, sink, "*")
        assert change(h, [physical("NOSUCH", "1")]).status == 400
        assert delivered(h, sink) == []
