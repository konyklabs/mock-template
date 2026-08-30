"""Two units, the same traffic, the same digest -- across every write path.

`Store.entity_digest` is the determinism evidence, and it ignores the values
of the vendor's `volatile_fields` -- at any depth, since konyklabs/roadmap#35
-- so two units seeded alike hash alike whatever the wall clock said. Each
write path that stamps something from the clock is driven here against two
units whose clocks are deliberately an hour apart, including the three whose
stamps live inside a list (a tender's `created_at`, a fulfillment's
`placed_at`) and were `xfail` until the digest reached them.
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


SUPPLIED_STAMP = "2026-08-30T10:00:00Z"


def order_with_supplied_stamps(h: Harness, stamp: str = SUPPLIED_STAMP) -> str:
    """A fulfillment whose `placed_at` and `expires_at` the caller sent, then
    a completion whose `picked_up_at` the caller sent too: three values the
    unit would otherwise have stamped from its clock."""
    created = order(
        h,
        fulfillments=[{"uid": "f1", "type": "PICKUP", "pickup_details": {"placed_at": stamp, "expires_at": stamp}}],
    )
    response = h.api.put(
        f"/v2/orders/{created}",
        {
            "idempotency_key": "det-complete",
            "order": {
                "version": 1,
                "fulfillments": [{"uid": "f1", "state": "COMPLETED", "pickup_details": {"picked_up_at": stamp}}],
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return created


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
        pytest.param(payment_against_order, id="payment-tenders-order"),
        pytest.param(pay_order_opaque, id="pay-order"),
        pytest.param(order_with_fulfillment, id="order-with-fulfillment"),
        pytest.param(order_with_supplied_stamps, id="order-with-caller-supplied-stamps"),
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


# ---------------------------------------------------------------------------
# A transition a wall-clock stamp marks is still state.
# ---------------------------------------------------------------------------


def test_a_spent_code_and_a_revoked_token_move_the_digest_by_their_mark_alone() -> None:
    """`used_at` and `revoked_at` are volatile -- their instants stay out of
    the digest -- but their *presence* is the only record that a code was
    spent or a token revoked. The measured hole this closes: the digest was
    byte-identical with the mark present and removed, so a mutant that
    stopped marking would not have moved it. Each mark is popped from the
    live map (no version bump, nothing else changes) and the digest must
    move."""
    from tests.unit.square.harness import APPLICATION_ID
    from vendorfake.square.entities import COL

    for h in build_harness("full"):
        store = h.unit.context.store
        code = h.code()
        token = h.token(
            client_secret=h.client_auth["authorization"].split()[1], grant_type="authorization_code", code=code
        )
        assert token.status == 200, token.text
        spent = store.raw(COL.codes)[code]
        assert spent.get("used_at")
        with_mark = store.entity_digest()
        spent.pop("used_at")
        assert store.entity_digest() != with_mark

        revoked = h.api.post(
            "/oauth2/revoke",
            {
                "client_id": APPLICATION_ID,
                "access_token": token.json()["access_token"],
                "revoke_only_access_token": True,
            },
            headers=h.client_auth,
        )
        assert revoked.status == 200, revoked.text
        record = next(
            e for e in store.raw(COL.tokens).values() if e.get("access_token") == token.json()["access_token"]
        )
        assert record.get("revoked_at")
        with_mark = store.entity_digest()
        record.pop("revoked_at")
        assert store.entity_digest() != with_mark


# ---------------------------------------------------------------------------
# A stamp the unit set is volatile; a value the caller sent is state.
# ---------------------------------------------------------------------------


def digest_with_supplied(stamp: str) -> str:
    for h in build_harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        order_with_supplied_stamps(h, stamp)
        return str(h.api.get("/__unit/state").json()["digest"])
    raise AssertionError("harness yielded nothing")


def test_two_units_differing_only_in_a_caller_supplied_stamp_digest_differently() -> None:
    """The reviewer's A/B: same clock, same traffic, and the only difference
    is the instant the *caller* sent for `placed_at`/`expires_at`/`picked_up_at`.
    Those names are volatile -- the unit sets them from its clock -- but a
    value the caller sent is state, mirrored into `supplied_stamps`, so the
    digests must differ."""
    assert digest_with_supplied("2026-08-30T10:00:00Z") != digest_with_supplied("2026-08-30T11:00:00Z")


def test_the_mirror_is_stored_and_digested_but_never_on_the_wire() -> None:
    from vendorfake.square.entities import COL

    for h in build_harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        order_id = order_with_supplied_stamps(h)
        stored = h.unit.context.store.raw(COL.orders)[order_id]["fulfillments"][0]
        assert stored["supplied_stamps"] == [
            ["expires_at", SUPPLIED_STAMP],
            ["picked_up_at", SUPPLIED_STAMP],
            ["placed_at", SUPPLIED_STAMP],
        ]
        wire = h.api.get(f"/v2/orders/{order_id}", headers=h.auth).json()["order"]["fulfillments"][0]
        assert "supplied_stamps" not in wire
        assert wire["pickup_details"]["picked_up_at"] == SUPPLIED_STAMP
        listed = h.api.post("/v2/orders/search", {"location_ids": [SEED_LOCATION_ID]}, headers=h.auth).json()
        assert all("supplied_stamps" not in f for o in listed["orders"] for f in o.get("fulfillments", []))

        # The mirror is what the digest sees: drop it from the live map and
        # the digest moves, while the volatile stamp beside it is ignored.
        with_mirror = h.unit.context.store.entity_digest()
        stored["pickup_details"]["picked_up_at"] = "1999-01-01T00:00:00Z"
        assert h.unit.context.store.entity_digest() == with_mirror
        stored.pop("supplied_stamps")
        assert h.unit.context.store.entity_digest() != with_mirror


def test_a_unit_set_stamp_is_not_mirrored_and_a_cleared_one_leaves_the_mirror() -> None:
    from vendorfake.square.entities import COL

    for h in build_harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        created = order(h, fulfillments=[{"uid": "f1", "type": "PICKUP", "pickup_details": {"note": "x"}}])
        stored = h.unit.context.store.raw(COL.orders)[created]["fulfillments"][0]
        assert "placed_at" in stored["pickup_details"]  # the unit stamped it
        assert "supplied_stamps" not in stored  # and mirrored nothing

        sent = h.api.put(
            f"/v2/orders/{created}",
            {
                "idempotency_key": "send",
                "order": {
                    "version": 1,
                    "fulfillments": [{"uid": "f1", "pickup_details": {"expires_at": SUPPLIED_STAMP}}],
                },
            },
            headers=h.auth,
        )
        assert sent.status == 200, sent.text
        stored = h.unit.context.store.raw(COL.orders)[created]["fulfillments"][0]
        assert stored["supplied_stamps"] == [["expires_at", SUPPLIED_STAMP]]

        cleared = h.api.put(
            f"/v2/orders/{created}",
            {
                "idempotency_key": "clear",
                "order": {"version": 2, "fulfillments": [{"uid": "f1", "pickup_details": {"expires_at": None}}]},
            },
            headers=h.auth,
        )
        assert cleared.status == 200, cleared.text
        stored = h.unit.context.store.raw(COL.orders)[created]["fulfillments"][0]
        assert "supplied_stamps" not in stored
        assert "expires_at" not in stored["pickup_details"]
