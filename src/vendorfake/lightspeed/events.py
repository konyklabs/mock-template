"""Committed state mutation -> Lightspeed webhook event.

FOR: deriving every webhook this unit sends from the journal, so an event
exists exactly when a mutation committed. **No handler emits**; the store's
journal decides, and this mapper keys on the journal's collection.

THE SEVEN EVENT TYPES are the specification's ``WebhookType`` enum, verified
identical to the list on https://x-series-api.lightspeedhq.com/docs/webhooks:
``sale.update``, ``product.update``, ``customer.update``, ``inventory.update``,
``register_closure.create``, ``consignment.send``, ``consignment.receive``.
:data:`LIGHTSPEED_EVENT_TYPES` is that enum in that order, and a subscription
may name any of them.

**Two of the seven are never fired.** Consignments are outside issue #94's
scoped surface, so nothing in this unit ever mutates a consignment and an
event with no mutation behind it would be a fake event with a real signature.
``capabilities.py`` records the omission under ``consignment-events``.

WHAT FIRES WHAT
---------------
============================  ==============================================
``register_closures`` insert  ``register_closure.create`` -- "fires every
                              register close". There is no REST resource for
                              a closure anywhere in the 135 documented paths,
                              so the payload is synthesised by the close
                              action itself (``surface/registers.py``).
``sales`` write               ``sale.update`` ("may fire multiple times for
                              layby/account sales")
``products`` write            ``product.update`` ("fires on product edits")
``customers`` write           ``customer.update`` ("create/delete/modify,
                              including balance changes")
``inventory`` write           ``inventory.update`` ("requires inventory
                              tracking enabled")
============================  ==============================================

``products``, ``customers``, ``inventory`` and ``sales`` each carry their own
model's wire shape, so the entity a webhook delivers and the entity the REST
route answers are ONE function and cannot drift.

A ``sale.update`` payload carries no payment-type NAME
(``PaymentTypeDetails.name``): the mapper projects from the journal entry and
has no reason to reach into the payment types collection for a label the
request itself did not carry. The payment's ``config_id`` is there, which is
the id a consumer resolves against ``GET /payment_types``.

A ``product.update`` and a ``customer.update`` fire on a DELETE as well as a
create or an edit, because both deletes here are soft: the row keeps its id,
gains a ``deleted_at``, and is still there for the payload to carry. The
webhooks page documents ``customer.update`` as covering
"create/delete/modify".

``inventory.update`` fires per inventory ROW, not per request: a stock
adjustment batch that moves three rows delivers three, and a product created
with opening stock at two outlets delivers two plus its ``product.update``.

THE PAYLOAD SHAPE is the 2026-07 entity as this unit stores it. DOCUMENTED
DEVIATION, and UNVERIFIED: the webhooks page says "The payload objects you'll
find in webhook requests are the same as those you'll receive from API 1.0" --
the OLDER response shapes, not the 2026-07 ones this specification documents.
This unit does not model API 1.0 at all, so it sends what it has. How large a
drift that implies is unknown and is recorded in ``capabilities.py`` under
``payload-shape-is-2026-07`` rather than guessed at.

THE DELIVERY FIELDS. ``PreparedEvent.body`` here is the *form fields* of the
delivery, not a JSON envelope: ``payload`` (the entity, which the signer
JSON-encodes), plus ``domain_prefix`` and ``environment``. The docs call the
latter two optional -- "may be present but are not guaranteed to be" -- and
this unit sends both, because a consumer whose code reads them should be
exercised against a delivery that has them. The form encoding itself is
``signer.encode_body``; see ``signer.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.core.kernel.types import EventMeta, JournalEntry, MappedEvent, UnitContext
from vendorfake.lightspeed.entities import COL, OBJECT_VERSION
from vendorfake.lightspeed.model.customer import project_customer
from vendorfake.lightspeed.model.inventory import project_inventory
from vendorfake.lightspeed.model.product import project_product
from vendorfake.lightspeed.model.sale import project_sale
from vendorfake.lightspeed.model.webhooks import (
    DOMAIN_PREFIX_FIELD,
    ENVIRONMENT_FIELD,
    PAYLOAD_FIELD,
    project_register_closure,
)
from vendorfake.lightspeed.surface.common import LightspeedDeps

__all__ = ["EVENT_FOR_COLLECTION", "LIGHTSPEED_EVENT_TYPES", "LightspeedEventMapper"]

LIGHTSPEED_EVENT_TYPES: tuple[str, ...] = (
    "sale.update",
    "product.update",
    "customer.update",
    "inventory.update",
    "register_closure.create",
    "consignment.send",
    "consignment.receive",
)
"""The specification's ``WebhookType`` enum, in its own order. All seven are
subscribable; the two consignment values are never fired here."""

EVENT_FOR_COLLECTION: Mapping[str, str] = {
    COL.register_closures: "register_closure.create",
    COL.sales: "sale.update",
    COL.products: "product.update",
    COL.customers: "customer.update",
    COL.inventory: "inventory.update",
}
"""Which committed collection produces which event. Read by the mapper and by
this package's tests, so the table is one thing rather than a chain of ``if``s
plus an assertion about it."""

#: Store bookkeeping the wire never carries. ``object_version`` is renamed
#: rather than dropped: it IS the Lightspeed ``version``.
_INTERNAL_KEYS = frozenset({"id", "version", "created_at", "updated_at", OBJECT_VERSION})


def _generic(entity: Mapping[str, Any]) -> dict[str, Any]:
    """A stored entity as the wire carries it: ``id`` first, the store's own
    bookkeeping dropped, and Lightspeed's ``version`` restored from
    :data:`~vendorfake.lightspeed.entities.OBJECT_VERSION`.

    No collection this unit writes falls back here any more -- every one of
    the five in :data:`_PROJECTIONS` names its own surface's model projection.
    It stays as the table's default, so a collection added by a later slice
    delivers its stored shape rather than raising at delivery time.
    """
    out: dict[str, Any] = {"id": entity["id"]}
    for key, value in entity.items():
        if key not in _INTERNAL_KEYS:
            out[key] = value
    out["version"] = entity.get(OBJECT_VERSION, 0)
    return out


_PROJECTIONS: Mapping[str, Any] = {
    COL.register_closures: project_register_closure,
    COL.products: project_product,
    COL.customers: project_customer,
    COL.inventory: project_inventory,
    COL.sales: project_sale,
}
"""Which committed collection is carried by which wire projection. A
collection absent from this table falls back to :func:`_generic`; today no
collection this unit writes is."""


class LightspeedEventMapper:
    """Satisfies ``EventMapper``. Holds the vendor for its configured
    ``domain_prefix`` and environment name."""

    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        event_type = EVENT_FOR_COLLECTION.get(entry.collection)
        if event_type is None:
            return ()
        stored = ctx.store.collection(entry.collection).get(entry.id)
        if stored is None:
            # A delete: the entity is gone, so there is nothing to carry. The
            # only collection here that is ever deleted from is `customers`,
            # whose event is documented to fire on delete -- a later slice
            # that models the deletion carries the tombstone it needs.
            return ()
        project = _PROJECTIONS.get(entry.collection, _generic)
        payload = project(stored)
        config = self._deps.config

        def build(_meta: EventMeta) -> object:
            return {
                PAYLOAD_FIELD: payload,
                DOMAIN_PREFIX_FIELD: config.domain_prefix,
                ENVIRONMENT_FIELD: config.webhook_environment,
            }

        return (MappedEvent(type=event_type, entity_id=entry.id, build=build),)
