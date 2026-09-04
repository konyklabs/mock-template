"""Committed state mutation -> Toast webhook envelope.

Derives every webhook from the journal, keyed on collection; no handler emits.

DOCUMENTED (``model/webhooks.py``): an ``orders``/``payments`` write is one
``order_updated`` carrying the full Order as ``GET /orders/{guid}`` answers it
("a new order is also considered an update", devOrdersWebhookRef.html); a
``stock`` write is ``in_stock``/``out_of_stock``/``low_quantity`` per the
documented status mapping and threshold (apiStockWebhook.html); a ``menus``
write is ``menus_updated`` with ``{restaurantGuid, publishedDate}``.

Envelope dates use the webhook ``...Z`` spelling except ``details.order``,
which keeps the REST ``+0000`` spelling since that document is "the full
Order as GET returns it".

JUDGMENT: the envelope ``guid`` is the dispatcher's event id (stable across
retries, for dedup) -- UUID-shaped but not version-4, so a consumer must
treat it as opaque. A payment write is mapped as an order update, since a
payment is only observable through its order; the orders surface also bumps
the order on every payment write, so one request can produce two envelopes,
both sent, since Toast documents no coalescing rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.core.kernel.types import EventMeta, JournalEntry, MappedEvent, UnitContext
from vendorfake.toast.entities import COL
from vendorfake.toast.model.dates import webhook_date
from vendorfake.toast.model.order import project_order
from vendorfake.toast.model.webhooks import CATEGORY_TYPES, EnvelopeWire, category_of
from vendorfake.toast.surface.common import ToastDeps
from vendorfake.toast.surface.payments import payments_for

__all__ = ["TOAST_EVENT_TYPES", "ToastEventMapper"]

TOAST_EVENT_TYPES: tuple[str, ...] = tuple(t for types in CATEGORY_TYPES.values() for t in types)
"""Every event type this unit can emit."""


class ToastEventMapper:
    """Satisfies ``EventMapper``. Holds the vendor for the low-quantity threshold."""

    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        if entry.collection == COL.orders:
            return self._order_updated(entry.id, ctx)
        if entry.collection == COL.payments:
            stored = ctx.store.collection(COL.payments).get(entry.id)
            order_guid = None if stored is None else stored.get("orderGuid")
            return self._order_updated(str(order_guid), ctx) if isinstance(order_guid, str) else ()
        if entry.collection == COL.stock:
            return self._stock(entry, ctx)
        if entry.collection == COL.menus:
            return self._menus_updated(entry, ctx)
        return ()

    def _order_updated(self, order_guid: str, ctx: UnitContext) -> Sequence[MappedEvent]:
        stored = ctx.store.collection(COL.orders).get(order_guid)
        if stored is None:
            return ()
        restaurant = str(stored.get("restaurant_guid", ""))
        order = project_order(stored, payments_for(ctx, stored))

        def build(meta: EventMeta) -> object:
            return _envelope(meta, "order_updated", {"restaurantGuid": restaurant, "order": order})

        return (MappedEvent(type="order_updated", entity_id=order_guid, build=build),)

    def _stock(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        stored = ctx.store.collection(COL.stock).get(entry.id)
        if stored is None:
            return ()
        status = str(stored.get("status", "IN_STOCK"))
        quantity = stored.get("quantity")
        if status == "OUT_OF_STOCK":
            event_type = "out_of_stock"
        elif (
            status == "QUANTITY"
            and isinstance(quantity, int | float)
            and quantity <= self._deps.config.low_quantity_threshold
        ):
            event_type = "low_quantity"
        else:
            event_type = "in_stock"
        details: dict[str, Any] = {
            "itemGuid": entry.id,
            "restaurantGuid": stored.get("restaurant_guid"),
            "status": status,
            "multiLocationId": stored.get("multiLocationId"),
            "versionId": stored.get("versionId"),
        }
        if status == "QUANTITY":
            details["quantity"] = quantity

        def build(meta: EventMeta) -> object:
            return _envelope(meta, event_type, details)

        return (MappedEvent(type=event_type, entity_id=entry.id, build=build),)

    def _menus_updated(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        stored = ctx.store.collection(COL.menus).get(entry.id)
        published = int(stored.get("lastUpdated", 0)) if stored is not None else int(ctx.clock.now())

        def build(meta: EventMeta) -> object:
            return _envelope(
                meta, "menus_updated", {"restaurantGuid": entry.id, "publishedDate": webhook_date(published)}
            )

        return (MappedEvent(type="menus_updated", entity_id=entry.id, build=build),)


def _envelope(meta: EventMeta, event_type: str, details: Mapping[str, Any]) -> dict[str, Any]:
    return EnvelopeWire(
        timestamp=meta.created_at,
        eventCategory=category_of(event_type) or event_type,
        eventType=event_type,
        guid=meta.event_id,
        details=dict(details),
    ).wire()
