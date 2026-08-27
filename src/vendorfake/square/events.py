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
becomes ``order.updated``. Everything else -- a delete, a mutation of any other
collection -- maps to nothing. Square publishes ``order.fulfillment.updated``
and ``order.updated`` variants this unit does not model, and it publishes no
order-deleted event at all, because Square orders are not deleted.

SHRINK (prototype): the OAuth, location and catalog collections emit nothing.
Square does publish ``oauth.authorization.revoked`` and catalog events; adding
them is a row in this mapper and a row in :data:`SQUARE_EVENT_TYPES`, which is
the shape this file is in so that the next event type is not a redesign.
"""

from __future__ import annotations

from collections.abc import Sequence

from vendorfake.core.kernel.types import EventMeta, JournalEntry, MappedEvent, UnitContext
from vendorfake.square.entities import COL, OrderEntity
from vendorfake.square.model.webhooks import (
    EventDataWire,
    EventEnvelopeWire,
    OrderCreatedSummary,
    OrderUpdatedSummary,
)

__all__ = ["ORDER_CREATED", "ORDER_UPDATED", "SQUARE_EVENT_TYPES", "SquareEventMapper"]

ORDER_CREATED = "order.created"
ORDER_UPDATED = "order.updated"

SQUARE_EVENT_TYPES: tuple[str, ...] = (ORDER_CREATED, ORDER_UPDATED)
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
}


class SquareEventMapper:
    """Satisfies ``EventMapper``. Stateless: the entry and the store are the input.

    Reads the *current* entity rather than reconstructing it from the journal
    entry, which is the reference's behaviour and the right one for a summary
    payload: the notification says what the order is now, and a consumer that
    re-reads it must not find a version older than the one it was told about.
    """

    __slots__ = ()

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
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
