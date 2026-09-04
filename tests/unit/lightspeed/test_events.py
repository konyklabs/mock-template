"""The event mapper: the seven names, what fires what, and what fires nothing."""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.entities import COL
from vendorfake.lightspeed.events import EVENT_FOR_COLLECTION, LIGHTSPEED_EVENT_TYPES
from vendorfake.lightspeed.model.webhooks import PAYLOAD_FIELD
from vendorfake.lightspeed.seed import constants as c

CLOSE = f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/close"


def test_the_seven_names_are_the_specifications_enum_in_its_own_order() -> None:
    assert LIGHTSPEED_EVENT_TYPES == (
        "sale.update",
        "product.update",
        "customer.update",
        "inventory.update",
        "register_closure.create",
        "consignment.send",
        "consignment.receive",
    )


def test_five_collections_map_to_events_and_two_names_have_none() -> None:
    """The consignment pair is subscribable and never fired: consignments are
    outside issue #94's scoped surface, and an event with no mutation behind it
    would be a fake event with a real signature."""
    assert set(EVENT_FOR_COLLECTION.values()) == set(LIGHTSPEED_EVENT_TYPES) - {
        "consignment.send",
        "consignment.receive",
    }
    assert EVENT_FOR_COLLECTION[COL.register_closures] == "register_closure.create"


def test_the_four_later_collections_are_already_keyed() -> None:
    """Products, inventory, customers and sales arrive in later slices; the
    mapper keys on them now so adding a surface is one projection rather than a
    second place to remember the event vocabulary."""
    assert EVENT_FOR_COLLECTION[COL.sales] == "sale.update"
    assert EVENT_FOR_COLLECTION[COL.products] == "product.update"
    assert EVENT_FOR_COLLECTION[COL.customers] == "customer.update"
    assert EVENT_FOR_COLLECTION[COL.inventory] == "inventory.update"


def test_a_seeded_insert_announces_nothing(h: Harness) -> None:
    """Seeded mutations carry ``{"seed": True}`` in their journal meta, which
    is what stops the dispatcher pushing an event for a record that existed
    before the process started."""
    assert h.deliveries() == []


def test_a_mutation_in_an_unmapped_collection_announces_nothing(h: Harness) -> None:
    """Registering a webhook is itself a mutation; the subscription collection
    is the core's and is excluded from the journal listener, so no event fires
    and a consumer is not told about their own subscription."""
    assert (
        h.post(
            h.path("/webhooks"),
            json.dumps({"active": True, "type": "sale.update", "url": "https://consumer.example/hooks/x"}),
        ).status
        == 201
    )
    assert h.deliveries() == []


def test_the_delivered_event_type_is_the_documented_name(h: Harness) -> None:
    assert h.put(h.path(CLOSE), "{}").status == 200
    # Settle first: the dispatcher hands off to a worker, so the delivery log
    # is written after the request has been answered.
    assert h.deliveries()
    records = h.api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [row["event_type"] for row in records] == ["register_closure.create"]


def test_a_subscription_on_another_event_receives_nothing(h: Harness) -> None:
    """The dispatcher's own matcher filters on the subscription's
    ``event_types``, which a Lightspeed webhook fills with exactly one value."""
    assert (
        h.put(
            h.path(f"/webhooks/{c.SEED_WEBHOOK_ID}"),
            json.dumps({"active": True, "type": "sale.update", "url": c.SEED_WEBHOOK_URL}),
        ).status
        == 200
    )
    assert h.put(h.path(CLOSE), "{}").status == 200
    assert h.deliveries() == []


def test_an_inactive_subscription_receives_nothing(h: Harness) -> None:
    assert (
        h.put(
            h.path(f"/webhooks/{c.SEED_WEBHOOK_ID}"),
            json.dumps({"active": False, "type": c.SEED_WEBHOOK_TYPE, "url": c.SEED_WEBHOOK_URL}),
        ).status
        == 200
    )
    assert h.put(h.path(CLOSE), "{}").status == 200
    assert h.deliveries() == []


def test_a_newly_created_subscription_receives_the_next_event(h: Harness) -> None:
    created = h.post(
        h.path("/webhooks"),
        json.dumps({"active": True, "type": c.SEED_WEBHOOK_TYPE, "url": "https://consumer.example/hooks/second"}),
    )
    assert created.status == 201
    assert h.put(h.path(CLOSE), "{}").status == 200
    urls = {delivered.url for delivered in h.deliveries()}
    assert urls == {c.SEED_WEBHOOK_URL, "https://consumer.example/hooks/second"}


def test_the_payload_carries_the_lightspeed_version(h: Harness) -> None:
    """Every entity carries its version, and a closure is an entity like any
    other -- so a consumer can order deliveries by it."""
    assert h.put(h.path(CLOSE), "{}").status == 200
    fields = dict(parse_qsl(h.deliveries()[0].body.decode("utf-8")))
    payload = json.loads(fields[PAYLOAD_FIELD])
    stored = h.unit.context.store.collection(COL.register_closures).all()[0]
    assert payload["version"] == stored["object_version"]
