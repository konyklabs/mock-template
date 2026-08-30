"""The notification envelope, field for field against Square's own examples.

The non-obvious part a rebuild gets wrong, and the reason this file exists:
an order event carries a **summary**, not the order. ``data.object`` holds one
key named after ``data.type``, and under it five scalars for ``order.created``
and six for ``order.updated``. Every other webhook system this project's authors
have met puts the entity there.

    https://developer.squareup.com/docs/webhooks/build-with-webhooks
    https://developer.squareup.com/reference/square/webhooks/order.created
    https://developer.squareup.com/reference/square/webhooks/order.updated

The mapper is driven here through a whole unit rather than with a hand-built
journal entry, because "an event exists exactly when a mutation committed" is
the claim, and a hand-built entry would be a claim about a dataclass.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.unit.square.harness import Harness
from tests.unit.square.harness import harness as build_harness
from vendorfake.core.kernel.types import EventMeta, JournalEntry
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.square.entities import COL
from vendorfake.square.events import ORDER_CREATED, ORDER_UPDATED, SQUARE_EVENT_TYPES, SquareEventMapper
from vendorfake.square.seed.constants import SEED_LOCATION_ID, SEED_MERCHANT_ID, SEED_OPEN_ORDER_ID

SUBSCRIBE = {
    "notification_url": "https://example.test/hooks",
    "event_types": ["*"],
    "signature_key": "test-signature-key",
}


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def h(sink: MemorySink) -> Iterator[Harness]:
    yield from build_harness("full", sink=sink)


def subscribe(h: Harness) -> dict[str, str]:
    response = h.api.post("/__unit/webhooks/subscriptions", SUBSCRIBE)
    assert response.status == 201
    return dict(response.json()["subscription"])


def delivered_bodies(h: Harness, sink: MemorySink) -> list[dict]:
    h.api.post("/__unit/webhooks/drain", {})
    return [json.loads(bytes(request.body).decode("utf-8")) for request in sink.received]


def create_order(h: Harness) -> str:
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": "evt-create",
            "order": {
                "location_id": SEED_LOCATION_ID,
                "line_items": [{"quantity": "1", "base_price_money": {"amount": 250}}],
            },
        },
        headers=h.auth,
    )
    assert response.status == 200
    return str(response.json()["order"]["id"])


# ---------------------------------------------------------------------------
# The envelope.
# ---------------------------------------------------------------------------


def test_order_created_reproduces_the_documented_envelope(h: Harness, sink: MemorySink) -> None:
    """https://developer.squareup.com/reference/square/webhooks/order.created

    Every key asserted, and the envelope's key *set* asserted as a whole, so an
    extra field this unit invented is as much a failure as a missing one.
    """
    subscribe(h)
    order_id = create_order(h)
    (body,) = delivered_bodies(h, sink)

    assert set(body) == {"merchant_id", "type", "event_id", "created_at", "data"}
    assert body["merchant_id"] == SEED_MERCHANT_ID
    assert body["type"] == ORDER_CREATED
    # "The idempotency (UUID) value that uniquely identifies the event."
    assert len(body["event_id"].split("-")) == 5
    assert body["created_at"].endswith("Z")

    data = body["data"]
    assert set(data) == {"type", "id", "object"}
    assert data["type"] == "order_created"
    assert data["id"] == order_id
    # The part a rebuild gets wrong: one key, named after `data.type`.
    assert list(data["object"]) == ["order_created"]
    assert data["object"]["order_created"] == {
        "created_at": data["object"]["order_created"]["created_at"],
        "location_id": SEED_LOCATION_ID,
        "order_id": order_id,
        "state": "OPEN",
        "version": 1,
    }


def test_the_created_summary_is_five_scalars_and_not_the_order(h: Harness, sink: MemorySink) -> None:
    """Stated as its own test because it is the single most likely thing to be
    "improved" into `data.object.order` by someone who has met other webhook
    systems. The order this unit created has line items and money roll-ups;
    none of them appears here."""
    subscribe(h)
    create_order(h)
    (body,) = delivered_bodies(h, sink)
    summary = body["data"]["object"]["order_created"]
    assert sorted(summary) == ["created_at", "location_id", "order_id", "state", "version"]
    assert all(not isinstance(value, dict | list) for value in summary.values())


def test_order_updated_adds_updated_at_and_carries_the_new_version(h: Harness, sink: MemorySink) -> None:
    """https://developer.squareup.com/reference/square/webhooks/order.updated

    `updated_at` is what makes a version bump observable without a re-read, and
    is the only field that distinguishes the two summaries on the wire.
    """
    subscribe(h)
    response = h.api.put(
        f"/v2/orders/{SEED_OPEN_ORDER_ID}",
        {"idempotency_key": "evt-update", "order": {"version": 1, "ticket_name": "Window"}},
        headers=h.auth,
    )
    assert response.status == 200
    (body,) = delivered_bodies(h, sink)

    assert body["type"] == ORDER_UPDATED
    assert body["data"]["type"] == "order_updated"
    summary = body["data"]["object"]["order_updated"]
    assert sorted(summary) == ["created_at", "location_id", "order_id", "state", "updated_at", "version"]
    assert summary["version"] == 2
    assert summary["state"] == "OPEN"
    assert summary["updated_at"]


def test_the_envelope_keys_are_in_the_documented_order(h: Harness, sink: MemorySink) -> None:
    """Key order is not decoration here: the delivered bytes are what the
    signature covers, so a reordered envelope is a different signature over the
    same information."""
    subscribe(h)
    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})
    raw = bytes(sink.received[0].body).decode("utf-8")
    assert raw.startswith('{"merchant_id":')
    assert list(json.loads(raw)) == ["merchant_id", "type", "event_id", "created_at", "data"]


# ---------------------------------------------------------------------------
# What is NOT mapped.
# ---------------------------------------------------------------------------


def test_only_the_orders_collection_produces_events(h: Harness, sink: MemorySink) -> None:
    """Registering a subscriber is a committed mutation of its own; it must not
    notify every subscriber that somebody subscribed. Neither must a token."""
    subscribe(h)
    subscribe(h)
    h.api.post("/oauth2/token/status", {"access_token": "nope"}, headers=h.client_auth)
    assert delivered_bodies(h, sink) == []


def test_a_deleted_entity_maps_to_nothing() -> None:
    """The mapper reads the *current* entity, so a delete has nothing to
    summarise. Building a payload from the journal entry alone would publish a
    partial order -- which is worse than publishing nothing, because a consumer
    would store it."""
    for h in build_harness("orders-only"):
        entry = JournalEntry(
            seq=1,
            at="2026-06-01T12:00:00.000Z",
            collection=COL.orders,
            id="CAISnot-a-real-order",
            op="delete",
            from_version=1,
            to_version=None,
            changed=(),
        )
        assert SquareEventMapper().map(entry, h.unit.context) == ()


def test_the_advertised_event_types_are_the_ones_the_mapper_can_produce(h: Harness, sink: MemorySink) -> None:
    """A type listed at `GET /v2/webhooks/event-types` with no branch in the
    mapper would be advertised and never sent. One mutation per collection the
    mapper reads, so every advertised type is observed at least once."""
    subscribe(h)
    order_id = create_order(h)
    h.api.put(
        f"/v2/orders/{SEED_OPEN_ORDER_ID}",
        {"idempotency_key": "evt-both", "order": {"version": 1, "ticket_name": "Bar"}},
        headers=h.auth,
    )
    paid = h.api.post(
        "/v2/payments",
        {
            "idempotency_key": "evt-pay",
            "source_id": "EXTERNAL",
            "amount_money": {"amount": 250},
            "order_id": order_id,
            "external_details": {"type": "OTHER", "source": "Kiosk"},
        },
        headers=h.auth,
    )
    assert paid.status == 200, paid.text
    observed = {body["type"] for body in delivered_bodies(h, sink)}
    assert observed == set(SQUARE_EVENT_TYPES)


def test_loading_the_scenario_emits_nothing(h: Harness, sink: MemorySink) -> None:
    """Subscribe first, then re-seed. A scenario with two orders in it must not
    push two `order.created` notifications: the dispatcher skips journal
    entries marked as seed writes, which is why a consumer can reset state
    between tests without their handler firing."""
    subscribe(h)
    assert h.api.post("/__unit/state/reset", {}).status == 200
    assert delivered_bodies(h, sink) == []


# ---------------------------------------------------------------------------
# The two-phase build.
# ---------------------------------------------------------------------------


def test_the_id_and_the_created_at_come_from_the_dispatcher() -> None:
    """The mapper names an event; the dispatcher assigns its id, because the id
    has to be stable across retries for a consumer to deduplicate on it. This
    drives `build` directly to show the seam does what it says."""
    for h in build_harness("orders-only"):
        stored = h.unit.context.store.collection(COL.orders).get(SEED_OPEN_ORDER_ID)
        assert stored is not None
        entry = JournalEntry(
            seq=7,
            at="2026-06-01T12:00:00.000Z",
            collection=COL.orders,
            id=SEED_OPEN_ORDER_ID,
            op="insert",
            from_version=None,
            to_version=1,
            changed=(),
        )
        (mapped,) = SquareEventMapper().map(entry, h.unit.context)
        assert mapped.type == ORDER_CREATED
        assert mapped.entity_id == SEED_OPEN_ORDER_ID
        built = mapped.build(EventMeta(event_id="assigned-by-the-dispatcher", created_at="2026-06-01T12:00:00.000Z"))
        assert isinstance(built, dict)
        assert built["event_id"] == "assigned-by-the-dispatcher"
        assert built["created_at"] == "2026-06-01T12:00:00.000Z"
