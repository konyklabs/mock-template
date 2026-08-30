"""Committed state mutation -> Clover webhook payload.

FOR: deriving every webhook this unit sends from the journal, so that an event
exists exactly when a mutation committed -- the same structural claim the
Square package makes, restated here because it is the one a rebuild dissolves
first: **no handler emits.** The orders, inventory, customers and payments
surfaces contain no ``emit`` call; the store's journal decides.

THE DOCUMENTED PAYLOAD, verbatim from https://docs.clover.com/dev/docs/webhooks
(fetched 2026-08-29)::

    {"appId":"DRKVJT2ZRRRSC",
     "merchants":{"XYZVJT2ZRRRSC":[{"objectId":"O:GHIVJT2ABCRSC","type":"CREATE","ts":1537970958000}]}}

* ``appId`` -- the app the subscription belongs to; this unit's ``client_id``.
* ``merchants`` -- keyed by merchant id, each a list of events.
* ``objectId`` -- ``<key>:<entity id>``. The documented keys are ``O`` orders,
  ``I`` inventory, ``C`` customers, ``P`` payments (``E`` employees and ``M``
  merchants exist and are not modelled: nothing here mutates either).
* ``type`` -- ``CREATE``, ``UPDATE`` or ``DELETE``.
* ``ts`` -- Unix **milliseconds**, like every Clover entity timestamp.

JUDGMENT -- **one event per delivery.** The documented shape is an aggregate
(a list per merchant), but Clover publishes nothing about when or whether it
batches, and a fake that invented a batching window would teach consumers a
coalescing rule that may not exist. Every delivery therefore carries exactly
one event in a one-element list; a consumer written against the documented
shape iterates and is correct either way.

JUDGMENT -- **what a soft delete looks like.** Clover's ``DELETE /orders/{id}``
is documented and the audit found no statement of whether the order is gone
or marked ``deletedTime``; the orders surface (PR C) records it as an
``update`` that sets ``deletedTime``, under the operation id ``DeleteOrder``.
Either signal maps to ``DELETE`` here, as does a hard ``delete`` journal op,
so the payload says ``DELETE`` however the surface chose to store it.

THE CONTRACT WITH THE SURFACES: this mapper keys on the journal's
``collection`` and ``op`` and on one ``operation_id``. The collections are
``entities.COL``'s ``orders``, ``items``, ``customers`` and ``payments``, read
from ``COL`` so the name is spelled once; the surfaces never call this module
and this module never calls a surface -- the store's journal is the whole
interface, which ``tests/unit/clover/test_events.py`` drives both through the
real routes and through raw store writes.

Event types on the core's side are ``<key>:<type>`` -- ``O:CREATE``,
``I:UPDATE`` -- so that a subscriber's per-key filter (Clover's dashboard lets
an app subscribe per key) is the core's own ``O:*`` glob and a chaos rule can
still name one exact event. See :data:`CLOVER_EVENT_TYPES`.
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
"""Store collection -> documented ``objectId`` prefix, keyed by the names
``entities.COL`` spells so a rename there is a rename here."""

CHANGE_TYPES: tuple[str, ...] = ("CREATE", "UPDATE", "DELETE")
"""The documented ``type`` vocabulary, in the order the page lists it."""

DELETE_OPERATION_IDS: frozenset[str] = frozenset({"DeleteOrder"})
"""Operation ids whose journal ``update`` is a soft delete. JUDGMENT -- see the
module docstring."""

SOFT_DELETE_FIELD = "deletedTime"
"""The field a soft-deleting write sets. Documented on the order object
(https://docs.clover.com/dev/docs/creating-custom-orders lists ``deletedTime``
beside ``createdTime`` and ``modifiedTime``)."""

VERIFICATION_EVENT_TYPE = "verification"
"""The event type of the one delivery that is not a state change: the
``{"verificationCode": ...}`` POST the webhook surface sends to a callback
being registered. Named here because the signer must recognise it -- the
``X-Clover-Auth`` header is documented as sent only *after* the callback is
validated, so this is the one delivery that must not carry it."""


def event_type(key: str, change: str) -> str:
    """``O`` + ``CREATE`` -> ``O:CREATE``. Spelled once."""
    return f"{key}:{change}"


def verification_event_id(subscription_id: str) -> str:
    """The event id of a subscription's verification POST.

    Load-bearing, not decoration: the signer recognises the unit's own
    verification delivery by this id *and* the type, because the type alone is
    forgeable -- ``POST /__unit/webhooks/emit`` accepts any type string -- and
    the id is not: the emitter derives its ids from a digest and nothing else
    in the process builds a :class:`PreparedEvent` with this shape.
    """
    return f"{VERIFICATION_EVENT_TYPE}:{subscription_id}"


CLOVER_EVENT_TYPES: tuple[str, ...] = tuple(
    event_type(key, change)
    for key in (KEY_ORDERS, KEY_INVENTORY, KEY_CUSTOMERS, KEY_PAYMENTS)
    for change in CHANGE_TYPES
)
"""Every event type this unit can emit: four keys times three changes."""


class CloverEventMapper:
    """Satisfies ``EventMapper``. Holds the vendor for ``appId``.

    ``appId`` is the resolved ``client_id``, read live through the vendor for
    the same reason every surface reads its config live: the profile's
    ``vendor`` block resolves in ``hydrate``, after this object is built.
    """

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        key = EVENT_KEYS.get(entry.collection)
        if key is None:
            return ()
        merchant_id = _merchant_id(entry, ctx)
        if merchant_id is None:
            # Not silently: a mutation nobody can be told about is a scenario
            # defect (a unit with no merchant), and the delivery log would
            # otherwise just be empty.
            ctx.log.warn(
                "webhook not mapped: no merchant to attribute the mutation to",
                {"collection": entry.collection, "id": entry.id, "seq": entry.seq},
            )
            return ()
        change = _change_type(entry, ctx)
        wire = EventWire(objectId=f"{key}:{entry.id}", type=change, ts=int(ctx.clock.now()))
        app_id = self._deps.config.client_id

        def build(meta: EventMeta) -> object:
            # `meta` carries the dispatcher's event id, which Clover's payload
            # has no field for; a consumer deduplicates on objectId + ts, and
            # the id stays on the delivery record for the fake's own log.
            return PayloadWire(appId=app_id, merchants={merchant_id: [wire]}).wire()

        return (MappedEvent(type=event_type(key, change), entity_id=entry.id, build=build),)


def _change_type(entry: JournalEntry, ctx: UnitContext) -> str:
    """``insert`` -> CREATE, ``delete`` -> DELETE, ``update`` -> UPDATE unless
    the write was a soft delete: a ``DeleteOrder`` operation, or a write that
    set ``deletedTime`` and left it set (an ``update`` that *cleared* it is not
    a delete, which is why the stored entity is consulted and not only the
    ``changed`` list)."""
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
    """The merchant the payload is keyed by.

    The entity's own ``merchant_id`` when it carries one (the package convention
    for the owning merchant, as on tokens and codes); otherwise the unit's
    merchant, of which there is one. On a hard delete the entity is gone and
    only the second source remains.
    """
    stored: Mapping[str, Any] | None = ctx.store.collection(entry.collection).get(entry.id)
    if stored is not None:
        owner = stored.get("merchant_id")
        if isinstance(owner, str) and owner:
            return owner
    merchants = ctx.store.collection(COL.merchants).all()
    if not merchants:
        return None
    return str(merchants[0]["id"])
