"""Committed state mutation -> Square notification.

FOR: deriving every webhook this unit sends from the journal, so that an event
exists exactly when a mutation committed.

INVARIANT: **no handler emits.** Note what is absent from
:mod:`vendorfake.square.surface.orders`: there is no ``emit`` call anywhere in
it. An event cannot exist for a create that was rejected, and a mutation cannot
silently skip its event, because neither the handler nor this mapper decides --
the store's journal does. That is the structural claim D-001 makes about
webhooks, and it is worth restating here because a rebuild that added a
convenience ``ctx.webhooks.emit(...)`` to a handler would dissolve it while
every test still passed.

The dispatcher supplies the rest of the contract: it filters out entries marked
as seed writes (loading a scenario with two orders in it must not push two
``order.created`` notifications), it mints the event id, and it will not
deliver at all without a signer.

WHAT IS MAPPED, AND WHAT IS NOT
-------------------------------
``insert`` on the orders collection becomes ``order.created``; ``update``
becomes ``order.updated``. The same pair on the payments collection becomes
``payment.created`` / ``payment.updated``, and those two carry the **whole
payment** under ``data.object.payment`` -- Square's documented shape for them
(https://developer.squareup.com/reference/square/webhooks/payment.created,
https://developer.squareup.com/reference/square/webhooks/payment.updated),
and the opposite of the order events' summary. Everything else -- a delete, a
mutation of any other collection -- maps to nothing. Square publishes
``order.fulfillment.updated`` and ``order.updated`` variants this unit does not
model, and it publishes no order-deleted event at all, because Square orders
are not deleted.

Two more, each carrying what its page documents and nothing more:

``catalog.version.updated``
    Every committed write to the catalog collection, with
    ``data.object.catalog_version.updated_at`` -- the one field Square's page
    shows (https://developer.squareup.com/reference/square/webhooks/catalog.version.updated).
    JUDGMENT, twice: one event per written object rather than one per
    request, because the journal is per object and an upsert of an item and
    two variations is three commits; and ``data.id`` carries the written
    object's id, where Square's example shows a placeholder.
``inventory.count.updated``
    Every committed change to a stock count, with the count under
    ``data.object.inventory_counts`` -- an array, as the page shows
    (https://developer.squareup.com/reference/square/webhooks/inventory.count.updated),
    holding the one count that changed. ``data.id`` is the count's store id.

SHRINK (prototype): the OAuth, location and loyalty collections emit nothing.
Square publishes ``oauth.authorization.revoked`` and the ``loyalty.*`` events;
adding one is a row in this mapper and a row in :data:`SQUARE_EVENT_TYPES`,
which is the shape this file is in so that the next event type is not a
redesign.
"""

from __future__ import annotations

from collections.abc import Sequence

from vendorfake.core.kernel.types import EventMeta, JournalEntry, MappedEvent, UnitContext
from vendorfake.square.config import SquareConfig
from vendorfake.square.entities import COL, InventoryCountEntity, OrderEntity, PaymentEntity
from vendorfake.square.model.payment import project_payment
from vendorfake.square.model.webhooks import (
    CatalogVersionUpdatedSummary,
    EventDataWire,
    EventEnvelopeWire,
    InventoryCountSummary,
    OrderCreatedSummary,
    OrderUpdatedSummary,
)
from vendorfake.square.surface.common import SquareDeps

__all__ = [
    "CATALOG_VERSION_UPDATED",
    "INVENTORY_COUNT_UPDATED",
    "ORDER_CREATED",
    "ORDER_UPDATED",
    "PAYMENT_CREATED",
    "PAYMENT_UPDATED",
    "SQUARE_EVENT_TYPES",
    "SquareEventMapper",
]

ORDER_CREATED = "order.created"
ORDER_UPDATED = "order.updated"
PAYMENT_CREATED = "payment.created"
PAYMENT_UPDATED = "payment.updated"
CATALOG_VERSION_UPDATED = "catalog.version.updated"
INVENTORY_COUNT_UPDATED = "inventory.count.updated"

SQUARE_EVENT_TYPES: tuple[str, ...] = (
    ORDER_CREATED,
    ORDER_UPDATED,
    PAYMENT_CREATED,
    PAYMENT_UPDATED,
    CATALOG_VERSION_UPDATED,
    INVENTORY_COUNT_UPDATED,
)
"""Every event type this unit can emit, in the order
``GET /v2/webhooks/event-types`` lists them.

Published rather than derived from the mapper's branches so that the listing
endpoint and the mapper cannot disagree: a type here with no branch would be
advertised and never sent, which a test asserts against.
"""

#: ``data.type`` for each event type. Square names the key inside
#: ``data.object`` after this value, so the two are one fact and are written
#: down once: ``order.created`` -> ``order_created`` -> ``object.order_created``.
_DATA_TYPES: dict[str, str] = {
    ORDER_CREATED: "order_created",
    ORDER_UPDATED: "order_updated",
    # Both payment events name their object `payment`, not `payment_created`:
    # the documented pages show ``"type": "payment"`` and the object under
    # ``data.object.payment`` is the full Payment.
    PAYMENT_CREATED: "payment",
    PAYMENT_UPDATED: "payment",
    CATALOG_VERSION_UPDATED: "catalog",
    INVENTORY_COUNT_UPDATED: "inventory",
}


class SquareEventMapper:
    """Satisfies ``EventMapper``. The entry and the store are the input.

    Reads the *current* entity rather than reconstructing it from the journal
    entry, which is the reference's behaviour and the right one for a summary
    payload: the notification says what the order is now, and a consumer that
    re-reads it must not find a version older than the one it was told about.

    Holds the vendor, as every surface does, for the one configured value a
    payload carries: ``application_details.application_id`` on a payment
    event. Read live so a profile's value is the one published. ``None`` --
    which a test building the mapper by hand may pass -- reads the default
    configuration instead.
    """

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps | None = None) -> None:
        self._deps = deps

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        if entry.collection == COL.payments:
            return self._payment(entry, ctx)
        if entry.collection == COL.catalog:
            return self._catalog(entry, ctx)
        if entry.collection == COL.inventory_counts:
            return self._inventory(entry, ctx)
        if entry.collection != COL.orders:
            return ()
        stored = ctx.store.collection(COL.orders).get(entry.id)
        if stored is None:
            # A delete, or an entity that vanished between the commit and the
            # listener. Nothing to summarise, and inventing a payload from the
            # journal entry alone would publish a partial order.
            return ()
        order = OrderEntity.from_entity(stored)
        if entry.op == "insert":
            return (_order_event(order, ORDER_CREATED),)
        if entry.op == "update":
            return (_order_event(order, ORDER_UPDATED),)
        return ()

    def _payment(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        stored = ctx.store.collection(COL.payments).get(entry.id)
        if stored is None or entry.op == "delete":
            return ()
        payment = PaymentEntity.from_entity(stored)
        event_type = PAYMENT_CREATED if entry.op == "insert" else PAYMENT_UPDATED
        config = SquareConfig() if self._deps is None else self._deps.config
        body = project_payment(payment, config.application_id)

        def build(meta: EventMeta) -> object:
            return EventEnvelopeWire(
                merchant_id=payment.merchant_id,
                type=event_type,
                event_id=meta.event_id,
                created_at=meta.created_at,
                data=EventDataWire(type=_DATA_TYPES[event_type], id=payment.id, object={"payment": body}),
            ).wire()

        return (MappedEvent(type=event_type, entity_id=payment.id, build=build),)

    def _catalog(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        stored = ctx.store.collection(COL.catalog).get(entry.id)
        if stored is None or entry.op == "delete":
            return ()
        merchant_id = _merchant_id(ctx)
        summary = CatalogVersionUpdatedSummary(updated_at=str(stored.get("updated_at", entry.at)))
        object_id = str(stored["id"])

        def build(meta: EventMeta) -> object:
            return EventEnvelopeWire(
                merchant_id=merchant_id,
                type=CATALOG_VERSION_UPDATED,
                event_id=meta.event_id,
                created_at=meta.created_at,
                data=EventDataWire(
                    type=_DATA_TYPES[CATALOG_VERSION_UPDATED],
                    id=object_id,
                    object={"catalog_version": summary.wire()},
                ),
            ).wire()

        return (MappedEvent(type=CATALOG_VERSION_UPDATED, entity_id=object_id, build=build),)

    def _inventory(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        stored = ctx.store.collection(COL.inventory_counts).get(entry.id)
        if stored is None or entry.op == "delete":
            return ()
        count = InventoryCountEntity.from_entity(stored)
        merchant_id = _merchant_id(ctx)
        summary = InventoryCountSummary(
            calculated_at=count.calculated_at or str(stored.get("updated_at", entry.at)),
            catalog_object_id=count.catalog_object_id,
            catalog_object_type=count.catalog_object_type,
            location_id=count.location_id,
            quantity=count.quantity,
            state=count.state,
        )

        def build(meta: EventMeta) -> object:
            return EventEnvelopeWire(
                merchant_id=merchant_id,
                type=INVENTORY_COUNT_UPDATED,
                event_id=meta.event_id,
                created_at=meta.created_at,
                data=EventDataWire(
                    type=_DATA_TYPES[INVENTORY_COUNT_UPDATED],
                    id=count.id,
                    object={"inventory_counts": [summary.wire()]},
                ),
            ).wire()

        return (MappedEvent(type=INVENTORY_COUNT_UPDATED, entity_id=count.id, build=build),)


def _merchant_id(ctx: UnitContext) -> str:
    """The seller, for an envelope whose entity carries no ``merchant_id``.

    A catalog object and a stock count belong to the seller rather than to a
    location, so the merchant is read from the one merchant the scenario
    seeds. Empty if a scenario seeds none, which no shipped one does.
    """
    merchants = ctx.store.collection(COL.merchants).all()
    return "" if not merchants else str(merchants[0]["id"])


def _order_event(order: OrderEntity, event_type: str) -> MappedEvent:
    """One named-but-not-yet-built event.

    Two phases because the id belongs to the dispatcher -- it must be stable
    across retries so a consumer can deduplicate on it -- while its position
    inside the envelope belongs here. The closure captures the order as it was
    when the journal entry committed, so a later mutation cannot rewrite an
    event already queued.
    """
    data_type = _DATA_TYPES[event_type]
    summary = (
        OrderCreatedSummary(
            created_at=order.created_at,
            location_id=order.location_id,
            order_id=order.id,
            state=order.state,
            version=order.version,
        )
        if event_type == ORDER_CREATED
        else OrderUpdatedSummary(
            created_at=order.created_at,
            location_id=order.location_id,
            order_id=order.id,
            state=order.state,
            updated_at=order.updated_at,
            version=order.version,
        )
    )

    def build(meta: EventMeta) -> object:
        return EventEnvelopeWire(
            merchant_id=order.merchant_id,
            type=event_type,
            event_id=meta.event_id,
            created_at=meta.created_at,
            data=EventDataWire(type=data_type, id=order.id, object={data_type: summary.wire()}),
        ).wire()

    return MappedEvent(type=event_type, entity_id=order.id, build=build)
