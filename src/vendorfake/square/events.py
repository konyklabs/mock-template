"""Committed state mutation -> Square notification.

INVARIANT: no handler emits; only the store's journal decides whether an event exists (D-001).

``insert``/``update`` on orders become ``order.created``/``order.updated`` (a summary); the same pair on
payments becomes ``payment.created``/``payment.updated`` and carries the whole payment; other mutations
and deletes map to nothing. https://developer.squareup.com/reference/square/webhooks/payment.created
https://developer.squareup.com/reference/square/webhooks/payment.updated

``catalog.version.updated`` fires per written catalog object. JUDGMENT: ``data.id`` carries the written
object's real id, where Square's example shows a placeholder.
https://developer.squareup.com/reference/square/webhooks/catalog.version.updated

``inventory.count.updated`` carries the changed count. JUDGMENT, NOT VERIFIED: ``data.id`` is this
unit's ``<variation id>:<location id>`` key.
https://developer.squareup.com/reference/square/webhooks/inventory.count.updated

SHRINK (prototype): OAuth, location and loyalty collections emit nothing.
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
"""Every event type this unit can emit, in ``GET /v2/webhooks/event-types`` order. Published rather
than derived from the mapper's branches, so the listing and the mapper cannot disagree."""

#: `data.type` for each event type; Square names the key inside `data.object` after this value.
_DATA_TYPES: dict[str, str] = {
    ORDER_CREATED: "order_created",
    ORDER_UPDATED: "order_updated",
    # Both payment events name their object `payment`, not `payment_created`.
    PAYMENT_CREATED: "payment",
    PAYMENT_UPDATED: "payment",
    CATALOG_VERSION_UPDATED: "catalog",
    INVENTORY_COUNT_UPDATED: "inventory",
}


class SquareEventMapper:
    """Satisfies ``EventMapper``. Reads the *current* entity rather than reconstructing it from the journal
    entry, so a notification always reflects the latest state; holds the vendor to read
    ``application_details.application_id`` live for a payment event."""

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
            # A delete, or an entity that vanished before the listener ran.
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
    """The seller, for an envelope whose entity carries no ``merchant_id``; empty if none seeded."""
    merchants = ctx.store.collection(COL.merchants).all()
    return "" if not merchants else str(merchants[0]["id"])


def _order_event(order: OrderEntity, event_type: str) -> MappedEvent:
    """One named-but-not-yet-built event; the closure captures the order as committed, so a later
    mutation cannot rewrite an event already queued."""
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
