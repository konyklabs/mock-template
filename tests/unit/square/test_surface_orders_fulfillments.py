"""Fulfillments on the Orders surface: the state machine, the sparse merge and
the stamps a transition sets.

https://developer.squareup.com/reference/square/objects/Fulfillment
https://developer.squareup.com/reference/square/enums/FulfillmentState
https://developer.squareup.com/docs/orders-api/manage-fulfillments

Where a test pins a stamp this unit sets on a transition, it says so: that
behaviour is the JUDGMENT recorded in ``surface/orders.py``, not a sentence
from the reference.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import SEED_LOCATION_ID, SEED_OPEN_ORDER_ID


@pytest.fixture
def h() -> Iterator[Harness]:
    """On a virtual clock: several tests below assert a stamp this unit sets
    inside the mutator against the store's own `updated_at`, and on a real
    clock those are two reads a millisecond apart."""
    yield from build_harness("orders-only", env={"VENDORFAKE_CLOCK": "virtual"})


def create(h: Harness, fulfillments: list[dict[str, Any]], key: str = "ff-create") -> dict[str, Any]:
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": key,
            "order": {
                "location_id": SEED_LOCATION_ID,
                "line_items": [{"quantity": "1", "base_price_money": {"amount": 500}}],
                "fulfillments": fulfillments,
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return dict(response.json()["order"])


def update(h: Harness, order: dict[str, Any], patch: dict[str, Any], key: str) -> Any:
    return h.api.put(
        f"/v2/orders/{order['id']}",
        {"idempotency_key": key, "order": {"version": order["version"], **patch}},
        headers=h.auth,
    )


PICKUP = {
    "uid": "pickup-1",
    "type": "PICKUP",
    "pickup_details": {"recipient": {"display_name": "Ada"}, "schedule_type": "ASAP", "note": "no lid"},
}


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_a_created_fulfillment_is_proposed_and_placed(h: Harness) -> None:
    """PROPOSED is the machine's initial state; `placed_at` is stamped on
    creation (JUDGMENT) and `line_item_application` is ALL, the only
    application this unit models."""
    order = create(h, [PICKUP])
    (fulfillment,) = order["fulfillments"]
    assert fulfillment["uid"] == "pickup-1"
    assert fulfillment["type"] == "PICKUP"
    assert fulfillment["state"] == "PROPOSED"
    assert fulfillment["line_item_application"] == "ALL"
    details = fulfillment["pickup_details"]
    assert details["recipient"] == {"display_name": "Ada"}
    assert details["schedule_type"] == "ASAP"
    assert details["note"] == "no lid"
    assert details["placed_at"] == order["created_at"]
    assert "delivery_details" not in fulfillment


def test_an_order_without_fulfillments_omits_the_key(h: Harness) -> None:
    """The entity half of the empty-array rule, as for `line_items`."""
    order = create(h, [])
    assert "fulfillments" not in order


def test_a_new_fulfillment_needs_a_documented_type(h: Harness) -> None:
    missing = h.api.post(
        "/v2/orders",
        {"idempotency_key": "ff-1", "order": {"location_id": SEED_LOCATION_ID, "fulfillments": [{"uid": "x"}]}},
        headers=h.auth,
    )
    assert missing.status == 400
    assert first_error(missing)["field"] == "order.fulfillments[0].type"

    unknown = h.api.post(
        "/v2/orders",
        {"idempotency_key": "ff-2", "order": {"location_id": SEED_LOCATION_ID, "fulfillments": [{"type": "DRONE"}]}},
        headers=h.auth,
    )
    assert unknown.status == 400
    assert first_error(unknown)["code"] == "INVALID_VALUE"


def test_details_for_the_wrong_type_are_refused(h: Harness) -> None:
    """A PICKUP fulfillment with `delivery_details` is a shape Square's object
    cannot hold; refused naming the field rather than stored."""
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": "ff-3",
            "order": {
                "location_id": SEED_LOCATION_ID,
                "fulfillments": [{"type": "PICKUP", "delivery_details": {"note": "x"}}],
            },
        },
        headers=h.auth,
    )
    assert response.status == 400
    assert first_error(response)["field"] == "order.fulfillments[0].delivery_details"


def test_a_uid_is_minted_when_the_caller_sends_none(h: Harness) -> None:
    order = create(h, [{"type": "DELIVERY", "delivery_details": {"recipient": {"display_name": "Bo"}}}])
    (fulfillment,) = order["fulfillments"]
    assert len(fulfillment["uid"]) == 22


# ---------------------------------------------------------------------------
# Transitions and stamps
# ---------------------------------------------------------------------------


def test_the_forward_path_stamps_each_step(h: Harness) -> None:
    """PROPOSED -> RESERVED -> PREPARED -> COMPLETED, the path the fulfillments
    guide walks. Each move stamps the details field Square documents for it
    -- `accepted_at`, `ready_at`, `picked_up_at` -- when the caller did not
    (JUDGMENT), and the order's version moves once per update."""
    order = create(h, [PICKUP])
    expected_version = order["version"]
    for state, stamp in (("RESERVED", "accepted_at"), ("PREPARED", "ready_at"), ("COMPLETED", "picked_up_at")):
        response = update(h, order, {"fulfillments": [{"uid": "pickup-1", "state": state}]}, key=f"ff-{state}")
        assert response.status == 200, response.text
        order = response.json()["order"]
        expected_version += 1
        assert order["version"] == expected_version
        (fulfillment,) = order["fulfillments"]
        assert fulfillment["state"] == state
        assert fulfillment["pickup_details"][stamp] == order["updated_at"]
    # The unmentioned details survived every step.
    assert order["fulfillments"][0]["pickup_details"]["note"] == "no lid"


def test_a_stamp_the_caller_sends_is_kept_rather_than_overwritten(h: Harness) -> None:
    """The consumer sends `picked_up_at` alongside `state: COMPLETED` and reads
    it back verbatim."""
    order = create(h, [PICKUP])
    response = update(
        h,
        order,
        {
            "fulfillments": [
                {"uid": "pickup-1", "state": "COMPLETED", "pickup_details": {"picked_up_at": "2026-08-30T10:00:00Z"}}
            ]
        },
        key="ff-stamp",
    )
    assert response.status == 200, response.text
    assert response.json()["order"]["fulfillments"][0]["pickup_details"]["picked_up_at"] == "2026-08-30T10:00:00Z"


def test_a_delivery_completion_stamps_delivered_at(h: Harness) -> None:
    order = create(h, [{"uid": "d1", "type": "DELIVERY", "delivery_details": {"deliver_at": "2026-08-30T12:00:00Z"}}])
    response = update(h, order, {"fulfillments": [{"uid": "d1", "state": "COMPLETED"}]}, key="ff-deliver")
    assert response.status == 200, response.text
    details = response.json()["order"]["fulfillments"][0]["delivery_details"]
    assert details["delivered_at"] == response.json()["order"]["updated_at"]
    assert details["deliver_at"] == "2026-08-30T12:00:00Z"


def test_a_skipped_state_is_accepted(h: Harness) -> None:
    """JUDGMENT recorded on the machine: PROPOSED straight to COMPLETED."""
    order = create(h, [PICKUP])
    response = update(h, order, {"fulfillments": [{"uid": "pickup-1", "state": "COMPLETED"}]}, key="ff-skip")
    assert response.status == 200, response.text
    assert response.json()["order"]["fulfillments"][0]["state"] == "COMPLETED"


def test_a_backward_move_is_refused_with_no_version_bump(h: Harness) -> None:
    """The invariant: a rejected request changes nothing. The transition is
    asserted before `Collection.update` runs."""
    order = create(h, [PICKUP])
    reserved = update(h, order, {"fulfillments": [{"uid": "pickup-1", "state": "PREPARED"}]}, key="ff-p").json()[
        "order"
    ]
    response = update(h, reserved, {"fulfillments": [{"uid": "pickup-1", "state": "PROPOSED"}]}, key="ff-back")
    assert response.status == 400
    assert response.json()["unit_error"]["kind"] == "invalid_transition"
    current = h.api.get(f"/v2/orders/{order['id']}", headers=h.auth).json()["order"]
    assert current["version"] == reserved["version"]
    assert current["fulfillments"][0]["state"] == "PREPARED"


def test_a_terminal_fulfillment_cannot_move(h: Harness) -> None:
    order = create(h, [PICKUP])
    canceled = update(h, order, {"fulfillments": [{"uid": "pickup-1", "state": "CANCELED"}]}, key="ff-c").json()[
        "order"
    ]
    assert canceled["fulfillments"][0]["pickup_details"]["canceled_at"] == canceled["updated_at"]
    response = update(h, canceled, {"fulfillments": [{"uid": "pickup-1", "state": "COMPLETED"}]}, key="ff-c2")
    assert response.status == 400
    assert response.json()["unit_error"]["kind"] == "invalid_transition"


def test_a_fulfillment_update_is_guarded_by_the_order_version(h: Harness) -> None:
    """Every mutation reads the order back first for `version`; a stale write
    is the documented VERSION_MISMATCH and changes nothing."""
    order = create(h, [PICKUP])
    stale = h.api.put(
        f"/v2/orders/{order['id']}",
        {
            "idempotency_key": "ff-stale",
            "order": {"version": 99, "fulfillments": [{"uid": "pickup-1", "state": "RESERVED"}]},
        },
        headers=h.auth,
    )
    assert stale.status == 400
    assert first_error(stale)["code"] == "VERSION_MISMATCH"
    assert (
        h.api.get(f"/v2/orders/{order['id']}", headers=h.auth).json()["order"]["fulfillments"][0]["state"] == "PROPOSED"
    )


# ---------------------------------------------------------------------------
# Sparse merge
# ---------------------------------------------------------------------------


def test_an_unmentioned_fulfillment_survives_and_an_entry_without_a_uid_appends(h: Harness) -> None:
    """A new fulfillment on update carries no uid and is minted one."""
    order = create(h, [PICKUP])
    response = update(
        h,
        order,
        {"fulfillments": [{"type": "SHIPMENT", "shipment_details": {"carrier": "USPS"}}]},
        key="ff-append",
    )
    assert response.status == 200, response.text
    fulfillments = response.json()["order"]["fulfillments"]
    assert fulfillments[0]["uid"] == "pickup-1"
    assert fulfillments[0]["pickup_details"]["note"] == "no lid"
    assert len(fulfillments[1]["uid"]) == 22
    assert fulfillments[1]["shipment_details"]["carrier"] == "USPS"
    assert fulfillments[1]["state"] == "PROPOSED"


def test_an_unknown_uid_on_update_is_refused_rather_than_created(h: Harness) -> None:
    """JUDGMENT, stated in `_fulfillment_patches` and unlike line items: a
    retry carrying a stale uid must not create a second fulfillment beside
    the one it meant to advance. Nothing is written and the version holds."""
    order = create(h, [PICKUP])
    response = update(
        h,
        order,
        {"fulfillments": [{"uid": "pickup-9", "type": "PICKUP", "state": "RESERVED"}]},
        key="ff-stale",
    )
    assert response.status == 400
    assert first_error(response)["field"] == "order.fulfillments[0].uid"
    assert response.json()["unit_error"]["known"] == ["pickup-1"]
    current = h.api.get(f"/v2/orders/{order['id']}", headers=h.auth).json()["order"]
    assert current["version"] == order["version"]
    assert [f["uid"] for f in current["fulfillments"]] == ["pickup-1"]


def test_a_null_details_field_is_cleared_and_an_absent_one_kept(h: Harness) -> None:
    """Both directions of the sparse rule, at the details level."""
    order = create(h, [PICKUP])
    response = update(
        h,
        order,
        {
            "fulfillments": [
                {"uid": "pickup-1", "pickup_details": {"note": None, "recipient": {"display_name": "Ada L."}}}
            ]
        },
        key="ff-clear",
    )
    assert response.status == 200, response.text
    details = response.json()["order"]["fulfillments"][0]["pickup_details"]
    assert "note" not in details
    assert details["recipient"] == {"display_name": "Ada L."}
    assert details["schedule_type"] == "ASAP"


def test_a_fulfillments_type_cannot_change(h: Harness) -> None:
    order = create(h, [PICKUP])
    response = update(h, order, {"fulfillments": [{"uid": "pickup-1", "type": "DELIVERY"}]}, key="ff-type")
    assert response.status == 400
    assert first_error(response)["field"] == "order.fulfillments[0].type"


def test_a_null_fulfillments_list_is_refused(h: Harness) -> None:
    """Unlike `line_items`, which a null empties: a fulfillment in flight is
    not something an update may silently discard."""
    order = create(h, [PICKUP])
    response = update(h, order, {"fulfillments": None}, key="ff-null")
    assert response.status == 400
    assert first_error(response)["field"] == "order.fulfillments"


def test_a_field_not_on_the_documented_page_is_dropped_not_stored(h: Harness) -> None:
    """`extra="ignore"` on the details models: a key the reference does not
    document is not echoed as though Square kept it."""
    order = create(h, [{"uid": "p", "type": "PICKUP", "pickup_details": {"note": "x", "made_up": "y"}}])
    assert "made_up" not in order["fulfillments"][0]["pickup_details"]


def test_the_machine_is_published_on_the_control_plane(h: Harness) -> None:
    """`GET /__unit/machines` lists `fulfillment` beside `order`, and the probe
    answers the same questions the surface does."""
    machines = h.api.get("/__unit/machines").json()["machines"]
    assert set(machines) >= {"order", "fulfillment"}
    ok = h.api.post("/__unit/machines/probe", {"machine": "fulfillment", "from": "PROPOSED", "to": "COMPLETED"})
    assert ok.status == 200
    refused = h.api.post("/__unit/machines/probe", {"machine": "fulfillment", "from": "COMPLETED", "to": "PROPOSED"})
    assert refused.status == 400


def test_the_seeded_open_order_has_no_fulfillments(h: Harness) -> None:
    order = h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=h.auth).json()["order"]
    assert "fulfillments" not in order
