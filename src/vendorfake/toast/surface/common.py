"""What every Toast surface is handed, and the helpers they share.

FOR: naming the dependency a surface has on its vendor -- the resolved
configuration and the unit's id stream -- as a protocol, so a surface module
never imports :mod:`vendorfake.toast.vendor` and the vendor is free to import
the surfaces.

INVARIANT: **a surface reads its configuration through the vendor, live.**
Both members of :class:`ToastDeps` are properties on the vendor object, not
values copied at route construction: a profile's ``vendor`` block resolves in
``hydrate``, which runs after the routes are built and again on every
``POST /__unit/state/reset``.

THE RESTAURANT HEADER. "``Toast-Restaurant-External-ID``: the GUID of the
restaurant ... It cannot be the GUID of a restaurant group"
(https://doc.toasttab.com/doc/devguide/apiOrdersGetDetailedInfoAboutOneOrder.html).
The auth adapter resolves it for every route whose ``auth`` is
``RESTAURANT_AUTH`` and records the guid on the ``AuthResult``;
:func:`require_restaurant` is how a handler reads it back, typed, without a
second parse of the header.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from vendorfake.core.kernel.types import HandlerArgs, UnitContext, UnitError, UnitErrorKind
from vendorfake.core.time.clock import Clock
from vendorfake.toast.config import ToastConfig
from vendorfake.toast.entities import COL, RestaurantEntity
from vendorfake.toast.ids import ToastIds

__all__ = [
    "BEARER_AUTH",
    "RESTAURANT_AUTH",
    "RESTAURANT_HEADER",
    "RESTAURANT_META_KEY",
    "ToastDeps",
    "int_param",
    "is_guid",
    "is_past_ms",
    "now_ms",
    "require_restaurant",
]

BEARER_AUTH = "bearer"
"""``Route.auth`` for a route that needs a token and no restaurant."""

RESTAURANT_AUTH = "restaurant"
"""``Route.auth`` for a restaurant-scoped route: a token AND the header."""

RESTAURANT_HEADER = "Toast-Restaurant-External-ID"
"""The documented header, in the documented casing."""

RESTAURANT_META_KEY = "restaurant_guid"
"""Where the auth adapter records the resolved restaurant on ``AuthResult.meta``."""

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_INTEGER = re.compile(r"^-?\d+$")


@runtime_checkable
class ToastDeps(Protocol):
    """What a surface needs from the vendor that owns it."""

    @property
    def config(self) -> ToastConfig: ...

    @property
    def ids(self) -> ToastIds: ...


def is_past_ms(instant_ms: int, clock: Clock) -> bool:
    """Whether the epoch-ms ``instant_ms`` is at or before the unit clock."""
    return instant_ms <= clock.now()


def now_ms(ctx: UnitContext) -> int:
    return int(ctx.clock.now())


def is_guid(value: str) -> bool:
    """The documented lowercase-UUID shape. ``GET /orders/{guid}`` documents a
    400 for "The GUID was malformed", which is what this decides."""
    return _GUID.match(value) is not None


def require_restaurant(args: HandlerArgs) -> RestaurantEntity:
    """The restaurant the auth adapter resolved for this request.

    A route declaring ``RESTAURANT_AUTH`` always has one by the time its
    handler runs; a route that did not declare it and calls this is a defect
    here, answered as the vendor's 500 rather than a plausible 4xx.
    """
    meta = args.auth.meta if args.auth is not None else None
    guid = None if meta is None else meta.get(RESTAURANT_META_KEY)
    if not isinstance(guid, str) or not guid:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail=f"{args.route.key} did not declare auth={RESTAURANT_AUTH!r} but its handler needs the restaurant.",
        )
    stored = args.ctx.store.collection(COL.restaurants).get(guid)
    if stored is None:
        raise UnitError(UnitErrorKind.INTERNAL, detail=f"The resolved restaurant {guid} is not in the store.")
    return RestaurantEntity.from_entity(stored)


def int_param(raw: str, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """``raw`` as an integer, or a 400 naming ``field``."""
    stripped = raw.strip()
    if not _INTEGER.match(stripped):
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{field} must be an integer.", field=field)
    value = int(stripped)
    if minimum is not None and value < minimum:
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{field} must be >= {minimum}.", field=field)
    if maximum is not None and value > maximum:
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{field} must be <= {maximum}.", field=field)
    return value


def strip_internal(entity: dict[str, Any]) -> dict[str, Any]:
    """A stored reference document minus the store's own bookkeeping keys, with
    ``id`` spelled ``guid`` first, the way every Toast document starts."""
    out: dict[str, Any] = {"guid": entity["id"]}
    for key, value in entity.items():
        if key in ("id", "version", "created_at", "updated_at"):
            continue
        out[key] = value
    return out
