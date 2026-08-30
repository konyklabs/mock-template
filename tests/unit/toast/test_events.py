"""The documented envelopes, driven through the real routes and the journal."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.toast.harness import Harness, harness
from tests.unit.toast.test_surface_orders import order_body
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.toast.events import TOAST_EVENT_TYPES
from vendorfake.toast.seed import constants as c
from vendorfake.toast.signer import verify_toast_signature

SECRET = "receiver-secret"
SUBSCRIBE = {"notification_url": "https://example.test/hooks", "event_types": ["*"], "signature_key": SECRET}
ENVELOPE_KEYS = ["timestamp", "eventCategory", "eventType", "guid", "details"]


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def h(sink: MemorySink) -> Iterator[Harness]:
    yield from harness("full", sink=sink)


def subscribe(h: Harness) -> None:
    assert h.api.post("/__unit/webhooks/subscriptions", SUBSCRIBE).status == 201


def delivered(h: Harness, sink: MemorySink) -> list[tuple[dict[str, str], dict[str, Any], bytes]]:
    h.api.post("/__unit/webhooks/drain", {})
    return [(dict(r.headers), json.loads(bytes(r.body).decode("utf-8")), bytes(r.body)) for r in sink.received]


def test_creating_an_order_delivers_one_order_updated_with_the_full_order_and_a_verifiable_signature(
    h: Harness, sink: MemorySink
) -> None:
    subscribe(h)
    order = h.post("/orders/v2/orders", order_body()).json()
    ((headers, body, raw),) = delivered(h, sink)
    assert list(body) == ENVELOPE_KEYS
    assert body["eventCategory"] == "order_updated" and body["eventType"] == "order_updated"
    assert body["timestamp"].endswith("Z") and len(body["timestamp"]) == 24
    assert body["details"]["restaurantGuid"] == c.SEED_RESTAURANT_GUID
    assert body["details"]["order"] == order  # "as GET returns it"
    assert headers["Toast-Event-Type"] == "order_updated" and headers["Toast-Event-Category"] == "order_updated"
    assert headers["Toast-Restaurant-External-ID"] == c.SEED_RESTAURANT_GUID
    assert headers["Toast-Attempt-Number"] == "1"
    assert headers["content-type"] == "application/json"
    assert verify_toast_signature(SECRET, raw, headers["Toast-Signature"])
    assert not verify_toast_signature("wrong", raw, headers["Toast-Signature"])


def test_every_order_mutation_is_an_update_and_a_payment_is_reported_through_its_order(
    h: Harness, sink: MemorySink
) -> None:
    subscribe(h)
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check = order["guid"], order["checks"][0]["guid"]
    h.post(
        f"/orders/v2/orders/{guid}/checks/{check}/selections", [{"item": {"guid": c.ITEM_LEMONADE_GUID}, "quantity": 1}]
    )
    h.post(
        f"/orders/v2/orders/{guid}/checks/{check}/payments",
        [{"type": "OTHER", "amount": 13.0, "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID}}],
    )
    h.post(f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": True}})
    bodies = [body for _, body, _ in delivered(h, sink)]
    assert all(body["eventType"] == "order_updated" for body in bodies)
    assert {body["details"]["order"]["guid"] for body in bodies} == {guid}
    # create, selections, payment insert + order update, void payment update + order update
    assert len(bodies) == 6
    assert bodies[-1]["details"]["order"]["voided"] is True
    assert bodies[-1]["details"]["order"]["checks"][0]["payments"][0]["paymentStatus"] == "VOIDED"


def test_stock_updates_map_to_the_three_documented_types_with_the_documented_details(
    h: Harness, sink: MemorySink
) -> None:
    subscribe(h)
    h.put(
        "/stock/v1/inventory/update",
        [
            {"guid": c.ITEM_SOUP_GUID, "status": "OUT_OF_STOCK"},
            {"guid": c.ITEM_BURGER_GUID, "status": "QUANTITY", "quantity": 5},
            {"guid": c.ITEM_LEMONADE_GUID, "status": "QUANTITY", "quantity": 10.0},
            {"guid": c.ITEM_SOUP_GUID, "status": "IN_STOCK"},
        ],
    )
    rows = delivered(h, sink)
    types = [(body["eventCategory"], body["eventType"]) for _, body, _ in rows]
    assert types == [("stock", "out_of_stock"), ("stock", "low_quantity"), ("stock", "in_stock"), ("stock", "in_stock")]
    out, low, counted, back = (body["details"] for _, body, _ in rows)
    assert out == {
        "itemGuid": c.ITEM_SOUP_GUID,
        "restaurantGuid": c.SEED_RESTAURANT_GUID,
        "status": "OUT_OF_STOCK",
        "multiLocationId": c.ITEM_SOUP_MULTI_LOCATION_ID,
        "versionId": "14707576-0000-4000-8000-00000000a201",
    }
    assert low["status"] == "QUANTITY" and low["quantity"] == 5.0  # "5 or less"
    assert counted["status"] == "QUANTITY" and counted["quantity"] == 10.0
    assert back["status"] == "IN_STOCK" and "quantity" not in back
    assert rows[0][0]["Toast-Event-Category"] == "stock" and rows[0][0]["Toast-Event-Type"] == "out_of_stock"


def test_a_menu_write_is_menus_updated(h: Harness, sink: MemorySink) -> None:
    subscribe(h)
    h.unit.context.store.collection("menus").update(
        c.SEED_RESTAURANT_GUID,
        lambda m: m.update({"lastUpdated": 1755786102000 + 60_000}),
        meta={"operation_id": "Publish"},
    )
    ((_, body, _),) = delivered(h, sink)
    assert body["eventCategory"] == "menus" and body["eventType"] == "menus_updated"
    assert body["details"] == {
        "restaurantGuid": c.SEED_RESTAURANT_GUID,
        "publishedDate": "2025-08-21T14:22:42.000+0000",
    }


def test_a_category_subscription_receives_only_its_types(h: Harness, sink: MemorySink) -> None:
    registered = h.api.post(
        "/__toast/webhooks/subscriptions", {"url": "https://example.test/stock", "eventCategories": ["stock"]}
    )
    assert registered.status == 201, registered.text
    h.post("/orders/v2/orders", order_body())
    h.put("/stock/v1/inventory/update", [{"guid": c.ITEM_SOUP_GUID, "status": "OUT_OF_STOCK"}])
    bodies = [body for _, body, _ in delivered(h, sink)]
    assert [body["eventType"] for body in bodies] == ["out_of_stock"]


def test_tokens_logins_and_subscriptions_deliver_nothing(h: Harness, sink: MemorySink) -> None:
    subscribe(h)
    from tests.unit.toast.test_surface_auth import LOGIN, LOGIN_PATH

    assert h.api.post(LOGIN_PATH, LOGIN).status == 200
    assert h.api.post("/__toast/webhooks/subscriptions", {"url": "https://example.test/second"}).status == 201
    assert delivered(h, sink) == []


def test_loading_the_scenario_emits_nothing(h: Harness, sink: MemorySink) -> None:
    subscribe(h)
    assert h.api.post("/__unit/state/reset", {}).status == 200
    entries = h.api.get("/__unit/journal").json()["entries"]
    assert any(e["collection"] in ("orders", "stock", "menus") for e in entries)
    assert delivered(h, sink) == []


def test_the_advertised_event_types_are_the_five_documented_ones() -> None:
    assert TOAST_EVENT_TYPES == ("order_updated", "in_stock", "out_of_stock", "low_quantity", "menus_updated")
