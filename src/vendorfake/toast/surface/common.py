"""What every Toast surface is handed, and the helpers they share.

Names a surface's dependency on its vendor -- resolved config and the id
stream -- as a protocol, so a surface never imports
:mod:`vendorfake.toast.vendor`.

INVARIANT: a surface reads its configuration through the vendor, live --
both :class:`ToastDeps` members are properties on the vendor object, since a
profile's ``vendor`` block resolves in ``hydrate``.

DOCUMENTED: "``Toast-Restaurant-External-ID``: the GUID of the restaurant ...
It cannot be the GUID of a restaurant group"
(https://doc.toasttab.com/doc/devguide/apiOrdersGetDetailedInfoAboutOneOrder.html);
the auth adapter resolves it,
and :func:`require_restaurant` reads it back, typed.
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
    """The documented lowercase-UUID shape (400: "The GUID was malformed")."""
    return _GUID.match(value) is not None


def require_restaurant(args: HandlerArgs) -> RestaurantEntity:
    """The restaurant the auth adapter resolved for this request; calling this
    from a route that did not declare ``RESTAURANT_AUTH`` is a defect here,
    answered as a 500 rather than a plausible 4xx."""
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
    """A stored entity minus bookkeeping keys, with ``id`` spelled ``guid`` first."""
    out: dict[str, Any] = {"guid": entity["id"]}
    for key, value in entity.items():
        if key in ("id", "version", "created_at", "updated_at"):
            continue
        out[key] = value
    return out
