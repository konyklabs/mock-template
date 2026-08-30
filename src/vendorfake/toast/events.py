"""Committed state mutation -> Toast webhook envelope.

FOR: deriving every webhook this unit sends from the journal, so an event
exists exactly when a mutation committed. **No handler emits**; the store's
journal decides, and this mapper keys on the journal's collection.

THE THREE CATEGORIES, all documented (``model/webhooks.py``):

* an ``orders`` write, or a ``payments`` write, is one ``order_updated`` for
  the order concerned, with ``details.order`` the full Order exactly as
  ``GET /orders/{guid}`` answers it ("A new order is also considered an
  update", devOrdersWebhookRef.html);
* a ``stock`` write is ``in_stock``, ``out_of_stock`` or ``low_quantity``
  with the documented details (apiStockWebhook.html): ``OUT_OF_STOCK`` ->
  ``out_of_stock``; ``IN_STOCK`` -> ``in_stock``; ``QUANTITY`` ->
  ``low_quantity`` when the quantity is at or under the configured threshold
  ("5 or less (currently 5)") and ``in_stock`` (with ``status: QUANTITY`` and
  the ``quantity``) otherwise;
* a ``menus`` write is ``menus_updated`` with ``{restaurantGuid, publishedDate}``.

The envelope's ``timestamp`` is the dispatcher's ``created_at`` -- the core
clock's RFC 3339 millisecond spelling, which is the documented webhook form
(``...Z``) -- and its ``guid`` is the dispatcher's event id, stable across
retries, which is what a consumer deduplicates on. ``details.order`` carries
the guest's ``customer`` and the ``deliveryInfo``: the subscription is the
partner's, and the scoping of those two blocks is documented on the REST GET,
not on the webhook (JUDGMENT).

The payments-collection write is mapped as an order update because a payment
is only ever observable through its order on Toast's wire; the orders surface
also bumps the order on every payment write, so the same request produces two
journal entries and two envelopes for one order. JUDGMENT: both are sent --
Toast documents "updates ... more than once" and no coalescing rule -- and a
consumer written for the documented at-least-once contract is correct either
way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.core.kernel.types import EventMeta, JournalEntry, MappedEvent, UnitContext
from vendorfake.toast.entities import COL
from vendorfake.toast.model.dates import rest_date
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
            return _envelope(meta, "menus_updated", {"restaurantGuid": entry.id, "publishedDate": rest_date(published)})

        return (MappedEvent(type="menus_updated", entity_id=entry.id, build=build),)


def _envelope(meta: EventMeta, event_type: str, details: Mapping[str, Any]) -> dict[str, Any]:
    return EnvelopeWire(
        timestamp=meta.created_at,
        eventCategory=category_of(event_type) or event_type,
        eventType=event_type,
        guid=meta.event_id,
        details=dict(details),
    ).wire()
