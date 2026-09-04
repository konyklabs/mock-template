"""Committed state mutation -> Clover webhook payload; every webhook is
derived from the journal, no handler emits.

DOCUMENTED: payload shape ``{"appId": ..., "merchants": {"<merchant id>":
[{"objectId": "<key>:<entity id>", "type": "CREATE"|"UPDATE"|"DELETE", "ts":
<unix ms>}]}}`` (https://docs.clover.com/dev/docs/webhooks). JUDGMENT: one
event per delivery; a soft delete (``deletedTime`` set) and a hard delete
both map to ``DELETE``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.clover.entities import COL
from vendorfake.clover.model.webhooks import EventWire, PayloadWire
from vendorfake.clover.surface.common import CloverDeps
from vendorfake.core.kernel.types import EventMeta, JournalEntry, MappedEvent, UnitContext

__all__ = [
    "CHANGE_TYPES",
    "CLOVER_EVENT_TYPES",
    "DELETE_OPERATION_IDS",
    "EVENT_KEYS",
    "KEY_CUSTOMERS",
    "KEY_INVENTORY",
    "KEY_ORDERS",
    "KEY_PAYMENTS",
    "VERIFICATION_EVENT_TYPE",
    "CloverEventMapper",
    "event_type",
    "verification_event_id",
]

KEY_ORDERS = "O"
KEY_INVENTORY = "I"
KEY_CUSTOMERS = "C"
KEY_PAYMENTS = "P"

EVENT_KEYS: Mapping[str, str] = {
    COL.orders: KEY_ORDERS,
    COL.items: KEY_INVENTORY,
    COL.customers: KEY_CUSTOMERS,
    COL.payments: KEY_PAYMENTS,
}
"""Store collection -> documented ``objectId`` prefix."""

CHANGE_TYPES: tuple[str, ...] = ("CREATE", "UPDATE", "DELETE")
"""The documented ``type`` vocabulary, in the order the page lists it."""

DELETE_OPERATION_IDS: frozenset[str] = frozenset({"DeleteOrder"})
"""Operation ids whose journal ``update`` is a soft delete. JUDGMENT."""

SOFT_DELETE_FIELD = "deletedTime"
"""The field a soft-deleting write sets (documented on the order object,
https://docs.clover.com/dev/docs/creating-custom-orders)."""

VERIFICATION_EVENT_TYPE = "verification"
"""The one delivery that is not a state change: a callback's verification POST."""


def event_type(key: str, change: str) -> str:
    return f"{key}:{change}"


def verification_event_id(subscription_id: str) -> str:
    """The signer keys on id+type together; the type alone is forgeable."""
    return f"{VERIFICATION_EVENT_TYPE}:{subscription_id}"


CLOVER_EVENT_TYPES: tuple[str, ...] = tuple(
    event_type(key, change)
    for key in (KEY_ORDERS, KEY_INVENTORY, KEY_CUSTOMERS, KEY_PAYMENTS)
    for change in CHANGE_TYPES
)
"""Every event type this unit can emit: four keys times three changes."""


class CloverEventMapper:
    """Satisfies ``EventMapper``. Reads ``client_id`` live through the vendor."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        key = EVENT_KEYS.get(entry.collection)
        if key is None:
            return ()
        merchant_id = _merchant_id(entry, ctx)
        if merchant_id is None:
            # Not silently: a unit with no merchant is a scenario defect.
            ctx.log.warn(
                "webhook not mapped: no merchant to attribute the mutation to",
                {"collection": entry.collection, "id": entry.id, "seq": entry.seq},
            )
            return ()
        change = _change_type(entry, ctx)
        wire = EventWire(objectId=f"{key}:{entry.id}", type=change, ts=int(ctx.clock.now()))
        app_id = self._deps.config.client_id

        def build(meta: EventMeta) -> object:
            # `meta`'s event id has no field in Clover's payload; dedup on objectId + ts.
            return PayloadWire(appId=app_id, merchants={merchant_id: [wire]}).wire()

        return (MappedEvent(type=event_type(key, change), entity_id=entry.id, build=build),)


def _change_type(entry: JournalEntry, ctx: UnitContext) -> str:
    """``update`` is DELETE only for a ``DeleteOrder`` op or a write leaving
    ``deletedTime`` set (checked against the stored entity, not ``changed``)."""
    if entry.op == "insert":
        return "CREATE"
    if entry.op == "delete":
        return "DELETE"
    operation_id = None if entry.meta is None else entry.meta.get("operation_id")
    if operation_id in DELETE_OPERATION_IDS:
        return "DELETE"
    if SOFT_DELETE_FIELD in entry.changed:
        stored = ctx.store.collection(entry.collection).get(entry.id)
        if stored is not None and stored.get(SOFT_DELETE_FIELD) is not None:
            return "DELETE"
    return "UPDATE"


def _merchant_id(entry: JournalEntry, ctx: UnitContext) -> str | None:
    """The entity's own ``merchant_id``, else the unit's one merchant (the
    only source left once a hard delete removes the entity)."""
    stored: Mapping[str, Any] | None = ctx.store.collection(entry.collection).get(entry.id)
    if stored is not None:
        owner = stored.get("merchant_id")
        if isinstance(owner, str) and owner:
            return owner
    merchants = ctx.store.collection(COL.merchants).all()
    if not merchants:
        return None
    return str(merchants[0]["id"])
