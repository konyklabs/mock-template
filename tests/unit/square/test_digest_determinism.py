"""Two units, the same traffic, the same digest -- across every write path.

`Store.entity_digest` is the determinism evidence, and it excludes the
vendor's `volatile_fields` so two units seeded alike hash alike whatever the
wall clock said. That claim held for the seed on its own; each write path
this branch added stamps something from the clock, and each is driven here
against two units whose clocks are deliberately an hour apart.

KNOWN GAP, konyklabs/roadmap#35: `volatile_fields` names top-level entity
fields only, so a clock stamp nested inside a list -- a tender's
`created_at`, a fulfillment's `placed_at` -- cannot be excluded. Those paths
are marked `xfail(strict=True)` against the issue: the test documents the
hole and turns red the day the mechanism closes it, so the marks get removed
rather than forgotten.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.unit.square.harness import Harness
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import (
    SEED_LOCATION_ID,
    SEED_LOYALTY_ACCOUNT_ID,
    SEED_LOYALTY_PROGRAM_ID,
    SEED_OPEN_ORDER_ID,
    TEA_ITEM_ID,
    TEA_MUG_VARIATION_ID,
)

Traffic = Callable[[Harness], None]

NESTED_STAMP = "konyklabs/roadmap#35: a wall-clock stamp nested in a list is beyond volatile_fields"


def order(h: Harness, amount: int = 500, **extra: Any) -> str:
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": "det-order",
            "order": {
                "location_id": SEED_LOCATION_ID,
                "line_items": [{"quantity": "1", "base_price_money": {"amount": amount}}],
                **extra,
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return str(response.json()["order"]["id"])


def catalog_upsert(h: Harness) -> None:
    response = h.api.post(
        "/v2/catalog/object",
        {"idempotency_key": "det-cat", "object": {"type": "ITEM", "id": "#Scone", "item_data": {"name": "Scone"}}},
        headers=h.auth,
    )
    assert response.status == 200, response.text


def catalog_update(h: Harness) -> None:
    current = h.api.get(f"/v2/catalog/object/{TEA_ITEM_ID}", headers=h.auth).json()["object"]
    response = h.api.post(
        "/v2/catalog/object",
        {
            "idempotency_key": "det-cat-2",
            "object": {
                "type": "ITEM",
                "id": TEA_ITEM_ID,
                "version": current["version"],
                "item_data": {"name": "Herbal"},
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text


def inventory_change(h: Harness) -> None:
    response = h.api.post(
        "/v2/inventory/changes/batch-create",
        {
            "idempotency_key": "det-inv",
            "changes": [
                {
                    "type": "PHYSICAL_COUNT",
                    "physical_count": {
                        "catalog_object_id": TEA_MUG_VARIATION_ID,
                        "location_id": SEED_LOCATION_ID,
                        "quantity": "7",
                    },
                }
            ],
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text


def loyalty_enrol_and_accumulate(h: Harness) -> None:
    enrolled = h.api.post(
        "/v2/loyalty/accounts",
        {
            "idempotency_key": "det-enrol",
            "loyalty_account": {"program_id": SEED_LOYALTY_PROGRAM_ID, "mapping": {"phone_number": "+14155550100"}},
        },
        headers=h.auth,
    )
    assert enrolled.status == 200, enrolled.text
    accumulated = h.api.post(
        f"/v2/loyalty/accounts/{SEED_LOYALTY_ACCOUNT_ID}/accumulate",
        {"idempotency_key": "det-acc", "location_id": SEED_LOCATION_ID, "accumulate_points": {"points": 3}},
        headers=h.auth,
    )
    assert accumulated.status == 200, accumulated.text


def payment_without_order(h: Harness) -> None:
    response = h.api.post(
        "/v2/payments",
        {
            "idempotency_key": "det-pay",
            "source_id": "EXTERNAL",
            "amount_money": {"amount": 250},
            "external_details": {"type": "OTHER", "source": "Kiosk"},
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text


def order_update(h: Harness) -> None:
    created = order(h)
    response = h.api.put(
        f"/v2/orders/{created}",
        {"idempotency_key": "det-upd", "order": {"version": 1, "ticket_name": "Bar"}},
        headers=h.auth,
    )
    assert response.status == 200, response.text


def payment_against_order(h: Harness) -> None:
    created = order(h)
    response = h.api.post(
        "/v2/payments",
        {
            "idempotency_key": "det-pay-order",
            "source_id": "EXTERNAL",
            "amount_money": {"amount": 500},
            "order_id": created,
            "external_details": {"type": "OTHER", "source": "Kiosk"},
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text


def pay_order_opaque(h: Harness) -> None:
    response = h.api.post(
        f"/v2/orders/{SEED_OPEN_ORDER_ID}/pay",
        {"idempotency_key": "det-payorder", "payment_ids": ["ext-1"]},
        headers=h.auth,
    )
    assert response.status == 200, response.text


def order_with_fulfillment(h: Harness) -> None:
    order(h, fulfillments=[{"uid": "f1", "type": "PICKUP", "pickup_details": {"note": "x"}}])


def digest_after(traffic: Traffic, *, advance_ms: int) -> str:
    for h in build_harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        if advance_ms:
            assert h.api.post("/__unit/clock/advance", {"ms": advance_ms}).status == 200
        traffic(h)
        return str(h.api.get("/__unit/state").json()["digest"])
    raise AssertionError("harness yielded nothing")


HOUR_MS = 60 * 60 * 1000


@pytest.mark.parametrize(
    "traffic",
    [
        pytest.param(catalog_upsert, id="catalog-upsert"),
        pytest.param(catalog_update, id="catalog-update"),
        pytest.param(inventory_change, id="inventory-change"),
        pytest.param(loyalty_enrol_and_accumulate, id="loyalty-enrol-accumulate"),
        pytest.param(payment_without_order, id="payment-no-order"),
        pytest.param(order_update, id="order-update"),
        pytest.param(
            payment_against_order, id="payment-tenders-order", marks=pytest.mark.xfail(strict=True, reason=NESTED_STAMP)
        ),
        pytest.param(pay_order_opaque, id="pay-order", marks=pytest.mark.xfail(strict=True, reason=NESTED_STAMP)),
        pytest.param(
            order_with_fulfillment,
            id="order-with-fulfillment",
            marks=pytest.mark.xfail(strict=True, reason=NESTED_STAMP),
        ),
    ],
)
def test_two_units_driven_alike_an_hour_apart_digest_alike(traffic: Traffic) -> None:
    """The second unit's clock is advanced an hour before the traffic, so
    every clock-derived field differs between the two; only the fields the
    vendor declares volatile can make the digests agree."""
    assert digest_after(traffic, advance_ms=0) == digest_after(traffic, advance_ms=HOUR_MS)


def test_the_clock_gap_is_real() -> None:
    """The control case: with `created_at` itself in the digest the two units
    would differ, so an implementation that stopped excluding anything cannot
    pass the test above by accident."""
    stamps = []
    for advance in (0, HOUR_MS):
        for h in build_harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
            if advance:
                h.api.post("/__unit/clock/advance", {"ms": advance})
            catalog_upsert(h)
            stamps.append(h.api.get(f"/v2/catalog/object/{TEA_ITEM_ID}", headers=h.auth).json()["object"]["updated_at"])
            listed = h.api.post("/v2/catalog/search", {"begin_time": "2026-01-01T00:00:00Z"}, headers=h.auth).json()
            stamps.append(listed["latest_time"])
    assert stamps[1] != stamps[3]
