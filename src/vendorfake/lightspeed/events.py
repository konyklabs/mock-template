"""Committed state mutation -> Lightspeed webhook event, keyed by the store's
journal so a delivery and the matching REST response can't drift.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/webhooks): the seven
event types are the specification's ``WebhookType`` enum
(:data:`LIGHTSPEED_EVENT_TYPES`); ``register_closure.create`` is synthesised
(no REST resource for a closure exists); ``product.update``/``customer.update``
also fire on a soft delete; ``inventory.update`` fires per row, not per request.

JUDGMENT / NOT VERIFIED: the two consignment types are never fired (out of
scope, see ``capabilities.py``'s ``consignment-events``); payloads use this
unit's 2026-07 shape rather than the API 1.0 shape the page describes (see
``capabilities.py``'s ``payload-shape-is-2026-07``); the optional
``domain_prefix``/``environment`` delivery fields are always sent.
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
"""The specification's ``WebhookType`` enum, in its own order; the two
consignment values are never fired here."""

EVENT_FOR_COLLECTION: Mapping[str, str] = {
    COL.register_closures: "register_closure.create",
    COL.sales: "sale.update",
    COL.products: "product.update",
    COL.customers: "customer.update",
    COL.inventory: "inventory.update",
}
"""Which committed collection produces which event."""

#: Store bookkeeping the wire never carries (``object_version`` renames to Lightspeed's ``version``).
_INTERNAL_KEYS = frozenset({"id", "version", "created_at", "updated_at", OBJECT_VERSION})


def _generic(entity: Mapping[str, Any]) -> dict[str, Any]:
    """A stored entity as the wire carries it: ``id`` first, store bookkeeping
    dropped, ``version`` restored from :data:`OBJECT_VERSION`. Fallback for a
    collection absent from :data:`_PROJECTIONS`.
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
"""Which committed collection is carried by which wire projection; falls back
to :func:`_generic`."""


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
            # A hard delete: nothing to carry (deletes modelled here are soft).
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
