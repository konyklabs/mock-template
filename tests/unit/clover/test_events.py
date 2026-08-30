"""The documented aggregate payload, byte for byte, and what maps to what.

The orders, inventory, customers and payments surfaces are not on this branch
(they arrive with PR C), so the mapper is driven the way the dispatcher drives
it: through journal entries the real store produces when a collection is
written. The contract with those surfaces is the collection names and one
operation id; both are written against the store here rather than against a
hand-built dataclass, so a rename on either side fails a test.

    https://docs.clover.com/dev/docs/webhooks
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.clover.harness import MERCHANT_ID, Harness, Silent
from tests.unit.clover.harness import harness as build_harness
from vendorfake.clover.entities import COL
from vendorfake.clover.events import CLOVER_EVENT_TYPES, EVENT_KEYS, CloverEventMapper
from vendorfake.clover.vendor import create_clover_vendor
from vendorfake.core.kernel.types import EventMeta, JournalEntry
from vendorfake.core.state.store import Store
from vendorfake.core.time.clock import Clock
from vendorfake.core.util.json import dump_json
from vendorfake.core.webhooks.sink import MemorySink

DOC_APP_ID = "DRKVJT2ZRRRSC"
DOC_MERCHANT_ID = "XYZVJT2ZRRRSC"
DOC_ORDER_ID = "GHIVJT2ABCRSC"
DOC_TS = 1537970958000
DOC_PAYLOAD = (
    b'{"appId":"DRKVJT2ZRRRSC","merchants":{"XYZVJT2ZRRRSC":'
    b'[{"objectId":"O:GHIVJT2ABCRSC","type":"CREATE","ts":1537970958000}]}}'
)
"""The example on the webhooks page, with its whitespace removed -- which is
what `dump_json`'s compact separators produce and what the delivery carries."""

SUBSCRIBE = {"notification_url": "https://example.test/hooks", "event_types": ["*"], "signature_key": "auth-code"}


def entry(collection: str, entity_id: str, op: str, *, changed: tuple[str, ...] = (), meta: Any = None) -> JournalEntry:
    return JournalEntry(
        seq=1,
        at="2026-08-30T12:00:00.000Z",
        collection=collection,
        id=entity_id,
        op=op,  # type: ignore[arg-type]
        from_version=None if op == "insert" else 1,
        to_version=None if op == "delete" else (1 if op == "insert" else 2),
        changed=changed,
        meta=meta,
    )


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def h(sink: MemorySink) -> Iterator[Harness]:
    yield from build_harness("full", sink=sink)


def subscribe(h: Harness) -> None:
    assert h.api.post("/__unit/webhooks/subscriptions", SUBSCRIBE).status == 201


def delivered_bodies(h: Harness, sink: MemorySink) -> list[dict[str, Any]]:
    h.api.post("/__unit/webhooks/drain", {})
    return [json.loads(bytes(request.body).decode("utf-8")) for request in sink.received]


# ---------------------------------------------------------------------------
# The documented bytes.
# ---------------------------------------------------------------------------


def test_the_mapper_reproduces_the_documented_payload_byte_for_byte() -> None:
    """A store on a virtual clock parked at the documented `ts`, the
    documented merchant and order, the documented app id -- and the delivered
    bytes are the page's example."""
    start = datetime.fromtimestamp(DOC_TS / 1000, tz=UTC).isoformat().replace("+00:00", "Z")
    clock = Clock("virtual", start)
    store = Store(clock)
    store.collection(COL.merchants).insert({"id": DOC_MERCHANT_ID, "name": "Doc merchant"}, {"seed": True})
    store.collection("orders").insert({"id": DOC_ORDER_ID, "state": "open"}, {"operation_id": "CreateOrder"})
    vendor = create_clover_vendor(vendor_config={"client_id": DOC_APP_ID})
    ctx: Any = SimpleNamespace(store=store, clock=clock, log=Silent())

    (mapped,) = CloverEventMapper(vendor).map(entry("orders", DOC_ORDER_ID, "insert"), ctx)  # type: ignore[arg-type]

    assert mapped.type == "O:CREATE"
    assert mapped.entity_id == DOC_ORDER_ID
    built = mapped.build(EventMeta(event_id="assigned-by-the-dispatcher", created_at=start))
    assert dump_json(built) == DOC_PAYLOAD
    assert isinstance(built, dict)
    assert list(built) == ["appId", "merchants"]
    assert list(built["merchants"][DOC_MERCHANT_ID][0]) == ["objectId", "type", "ts"]


def test_ts_is_an_integer_of_milliseconds_not_a_float_and_not_seconds(h: Harness, sink: MemorySink) -> None:
    """Clock.now() is a float of milliseconds; the documented `ts` is an
    integer of them. A float would put `1537970958000.0` on the wire and
    seconds would be off by a thousand -- both silent to a consumer."""
    subscribe(h)
    before = h.unit.context.clock.now()
    h.unit.context.store.collection("orders").insert({"id": "ORD0000000001"}, {"operation_id": "CreateOrder"})
    (body,) = delivered_bodies(h, sink)
    ts = body["merchants"][MERCHANT_ID][0]["ts"]
    assert isinstance(ts, int)
    assert before - 1000 <= ts <= h.unit.context.clock.now() + 1000


# ---------------------------------------------------------------------------
# What maps to what.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("collection", "key"),
    [("orders", "O"), ("items", "I"), ("customers", "C"), ("payments", "P")],
)
def test_each_agreed_collection_maps_to_its_documented_key(
    h: Harness, sink: MemorySink, collection: str, key: str
) -> None:
    subscribe(h)
    h.unit.context.store.collection(collection).insert({"id": "ENT0000000001"}, {"operation_id": "Create"})
    (body,) = delivered_bodies(h, sink)
    assert body["merchants"][MERCHANT_ID] == [
        {"objectId": f"{key}:ENT0000000001", "type": "CREATE", "ts": body["merchants"][MERCHANT_ID][0]["ts"]}
    ]
    assert EVENT_KEYS[collection] == key


def test_insert_update_and_delete_map_to_the_three_documented_types(h: Harness, sink: MemorySink) -> None:
    subscribe(h)
    orders = h.unit.context.store.collection("orders")
    orders.insert({"id": "ORD0000000001", "title": "a"}, {"operation_id": "CreateOrder"})
    orders.update("ORD0000000001", lambda order: order.update({"title": "b"}), meta={"operation_id": "UpdateOrder"})
    orders.delete("ORD0000000001", {"operation_id": "HardDelete"})
    bodies = delivered_bodies(h, sink)
    assert [body["merchants"][MERCHANT_ID][0]["type"] for body in bodies] == ["CREATE", "UPDATE", "DELETE"]
    assert {body["merchants"][MERCHANT_ID][0]["objectId"] for body in bodies} == {"O:ORD0000000001"}


def test_a_soft_delete_maps_to_delete_by_operation_id_or_by_deleted_time(h: Harness, sink: MemorySink) -> None:
    """Both signals PR C might send, each on its own: the `DeleteOrder`
    operation id with any write, and a write that sets `deletedTime` under
    some other operation id."""
    subscribe(h)
    orders = h.unit.context.store.collection("orders")
    orders.insert({"id": "ORD0000000001"}, {"operation_id": "CreateOrder"})
    orders.insert({"id": "ORD0000000002"}, {"operation_id": "CreateOrder"})
    orders.update("ORD0000000001", lambda order: order.update({"state": "gone"}), meta={"operation_id": "DeleteOrder"})
    orders.update(
        "ORD0000000002",
        lambda order: order.update({"deletedTime": 1700000000000}),
        meta={"operation_id": "UpdateOrder"},
    )
    types = [body["merchants"][MERCHANT_ID][0]["type"] for body in delivered_bodies(h, sink)]
    assert types == ["CREATE", "CREATE", "DELETE", "DELETE"]


def test_clearing_deleted_time_is_an_update_not_a_delete(h: Harness, sink: MemorySink) -> None:
    """`deletedTime` in the changed list is not enough on its own: the write
    that *removes* it is a restore."""
    subscribe(h)
    orders = h.unit.context.store.collection("orders")
    orders.insert({"id": "ORD0000000001", "deletedTime": 1700000000000}, {"operation_id": "CreateOrder"})
    orders.update("ORD0000000001", lambda order: order.pop("deletedTime"), meta={"operation_id": "UpdateOrder"})
    types = [body["merchants"][MERCHANT_ID][0]["type"] for body in delivered_bodies(h, sink)]
    assert types == ["CREATE", "UPDATE"]


def test_one_event_per_delivery_never_a_batch(h: Harness, sink: MemorySink) -> None:
    """JUDGMENT: batching is undocumented, so three mutations are three
    deliveries of one-element lists rather than one delivery of three."""
    subscribe(h)
    orders = h.unit.context.store.collection("orders")
    for n in range(3):
        orders.insert({"id": f"ORD000000000{n}"}, {"operation_id": "CreateOrder"})
    bodies = delivered_bodies(h, sink)
    assert len(bodies) == 3
    assert all(len(body["merchants"][MERCHANT_ID]) == 1 for body in bodies)
    assert all(list(body["merchants"]) == [MERCHANT_ID] for body in bodies)


def test_the_app_id_is_the_profiles_client_id_read_live(h: Harness, sink: MemorySink) -> None:
    """The `full` profile's vendor block sets `client_id`; the mapper must
    report that rather than the built-in default captured at construction."""
    subscribe(h)
    h.unit.context.store.collection("orders").insert({"id": "ORD0000000001"}, {"operation_id": "CreateOrder"})
    (body,) = delivered_bodies(h, sink)
    assert body["appId"] == "UNITCLOVERAPP" == h.unit.context.vendor.config.client_id  # type: ignore[attr-defined]


def test_the_entity_s_own_merchant_wins_over_the_unit_s_merchant(h: Harness, sink: MemorySink) -> None:
    subscribe(h)
    h.unit.context.store.collection("orders").insert(
        {"id": "ORD0000000001", "merchant_id": "OTHERMERCHANT"}, {"operation_id": "CreateOrder"}
    )
    (body,) = delivered_bodies(h, sink)
    assert list(body["merchants"]) == ["OTHERMERCHANT"]


def test_other_collections_map_to_nothing(h: Harness, sink: MemorySink) -> None:
    """Tokens, codes, merchants and subscriptions are mutations too; none is
    a documented event key."""
    subscribe(h)
    subscribe_again = h.api.post(
        "/__unit/webhooks/subscriptions", {**SUBSCRIBE, "notification_url": "https://second.test/hooks"}
    )
    assert subscribe_again.status == 201
    h.exchange()  # mints a code and a token through the OAuth surface
    h.unit.context.store.collection(COL.merchants).update(MERCHANT_ID, lambda m: m.update({"name": "Renamed"}))
    assert delivered_bodies(h, sink) == []


def test_a_mutation_in_a_unit_with_no_merchant_is_not_delivered_and_is_logged(sink: MemorySink) -> None:
    """Nothing to key the payload by. Dropped with a warning rather than
    delivered under an invented merchant id a consumer would store."""
    warnings: list[str] = []

    class Recording(Silent):
        def warn(self, msg: str, fields: Any = None) -> None:
            warnings.append(msg)

    from vendorfake import create_unit

    unit = create_unit(vendor="clover", profile="full", sink=sink, logger=Recording())
    try:
        unit.context.store.collection("subscriptions").insert(SUBSCRIBE | {"id": "wbhk_test"}, {"source": "test"})
        unit.context.store.collection("orders").insert({"id": "ORD0000000001"}, {"operation_id": "CreateOrder"})
        unit.context.webhooks.drain()
    finally:
        unit.stop()
    assert sink.received == []
    assert any("no merchant" in message for message in warnings)


def test_the_advertised_event_types_are_the_key_type_product() -> None:
    assert len(CLOVER_EVENT_TYPES) == 12
    assert CLOVER_EVENT_TYPES[:3] == ("O:CREATE", "O:UPDATE", "O:DELETE")
    assert {t.split(":")[0] for t in CLOVER_EVENT_TYPES} == set(EVENT_KEYS.values())


# ---------------------------------------------------------------------------
# Through the control plane, which is how a test without PR C's routes commits
# a versioned update.
# ---------------------------------------------------------------------------


def test_a_control_plane_state_update_delivers_an_update_event(h: Harness, sink: MemorySink) -> None:
    subscribe(h)
    h.unit.context.store.collection("orders").insert({"id": "ORD0000000001"}, {"operation_id": "CreateOrder"})
    updated = h.api.post(
        "/__unit/state/update",
        {"collection": "orders", "id": "ORD0000000001", "version": 1, "patch": {"note": "table 4"}},
    )
    assert updated.status == 200, updated.text
    types = [body["merchants"][MERCHANT_ID][0]["type"] for body in delivered_bodies(h, sink)]
    assert types == ["CREATE", "UPDATE"]


def test_seed_writes_deliver_nothing(h: Harness, sink: MemorySink) -> None:
    """The harness's merchant insert is marked as a seed write; so will PR E's
    scenario be. Loading a scenario with an open order in it must not push an
    O:CREATE to every subscriber."""
    subscribe(h)
    h.unit.context.store.collection("orders").insert({"id": "ORD0000000001"}, {"operation_id": "Seed", "seed": True})
    assert delivered_bodies(h, sink) == []
