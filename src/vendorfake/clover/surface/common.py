"""What every Clover surface is handed, and the time helpers they share.

FOR: naming the dependency a surface has on its vendor -- the resolved
configuration and the unit's id stream -- as a protocol, so a surface module
never imports :mod:`vendorfake.clover.vendor` and the vendor is free to import
the surfaces.

INVARIANT: **a surface reads its configuration through the vendor, live.**
Both members of :class:`CloverDeps` are properties on the vendor object, not
values copied at route construction: a profile's ``vendor`` block resolves in
``hydrate``, which runs *after* the routes are built and again on every
``POST /__unit/state/reset``. A surface that captured ``config.client_id``
when its routes were made would authenticate against the default secret
forever. The same holds harder for ``ids``: there is exactly one id stream per
unit, re-seeded at hydrate.

The two time helpers are the only place this package converts between its
units. Entities store epoch **milliseconds** (the core clock's unit, and
Clover's own for ``createdTime``/``modifiedTime``); the documented OAuth wire
carries Unix **seconds** (``"access_token_expiration": 1677875430``,
https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token).
:func:`wire_seconds` is that conversion, spelled once, floor division so a
value never rounds up into a second that has not arrived.

:func:`is_past_ms` answers "has this stored instant passed?" for both the auth
adapter and the OAuth surface, with the same boundary the Square package
settled on: **at or before** is expired, so a consumer that advances a virtual
clock by exactly the TTL sees the token gone rather than landing on the one
millisecond where it still works.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from vendorfake.clover.config import CloverConfig
from vendorfake.clover.ids import CloverIds
from vendorfake.core.kernel.types import HandlerArgs, UnitError, UnitErrorKind
from vendorfake.core.time.clock import Clock

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_EXPANSIONS",
    "MAX_LIMIT",
    "CloverDeps",
    "elements",
    "expansions",
    "int_param",
    "is_past_ms",
    "page_window",
    "require_merchant",
    "wire_seconds",
]

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
""""limit" default 100 and maximum 1000, with "offset" -- documented for both
the orders and the items lists (https://docs.clover.com/dev/docs/ordergetorders,
https://docs.clover.com/dev/docs/inventorygetitems)."""


@runtime_checkable
class CloverDeps(Protocol):
    """What a surface needs from the vendor that owns it."""

    @property
    def config(self) -> CloverConfig:
        """The resolved configuration, re-read on every access."""
        ...

    @property
    def ids(self) -> CloverIds:
        """This unit's one id stream."""
        ...


def is_past_ms(instant_ms: int, clock: Clock) -> bool:
    """Whether the epoch-ms ``instant_ms`` is at or before the unit clock."""
    return instant_ms <= clock.now()


def wire_seconds(instant_ms: int) -> int:
    """An epoch-ms instant as the Unix-seconds integer the OAuth wire carries.

    Floor division, deliberately: truncating matches the Square package's
    iso-seconds convention (never round up into a second that has not
    happened) and keeps ``wire_seconds(is_past_ms boundary)`` on the safe
    side.
    """
    return instant_ms // 1000


def require_merchant(args: HandlerArgs) -> str:
    """The ``{mId}`` of the request, checked against the bearer's merchant.

    JUDGMENT -- a token presented against another merchant's path, or an
    unknown one, answers **401**: Clover documents no error for the mismatch,
    and the conflation rule (https://docs.clover.com/dev/docs/401-unauthorized)
    makes "this token is not good for this" a 401 whatever the reason. No
    detail, so the wire body is the same ``401 Unauthorized`` every other
    authorization failure sends; the sidecar says ``merchant_mismatch``.
    """
    merchant_id = args.params["mId"]
    principal = args.auth.principal_id if args.auth is not None else None
    if principal != merchant_id:
        raise UnitError(
            UnitErrorKind.UNAUTHORIZED,
            info={"reason": "merchant_mismatch", "path_merchant": merchant_id},
        )
    return merchant_id


_INTEGER = re.compile(r"^-?\d+$")
"""One optional minus, then digits. ``--5``, ``+5``, ``5x`` and ``1e3`` are
all refused: ``str.isdigit`` after ``lstrip("-")`` let ``--5`` through to
``int()`` and a 500."""


def int_param(raw: str, field: str, *, minimum: int | None = None) -> int:
    """``raw`` as an integer, or a 400 naming ``field``.

    The one integer parser for query strings and filter values, so every
    surface refuses the same spellings the same way. Refused rather than
    ignored: ``limit=abc`` silently becoming the default is how a consumer
    who mistyped a page size never learns.
    """
    stripped = raw.strip()
    if not _INTEGER.match(stripped):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be an integer.",
            field=field,
            info={"supplied": raw},
        )
    value = int(stripped)
    if minimum is not None and value < minimum:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be an integer >= {minimum}.",
            field=field,
            info={"supplied": raw},
        )
    return value


def _query_int(args: HandlerArgs, name: str, default: int, *, minimum: int) -> int:
    raw = args.query(name)
    if raw is None:
        return default
    return int_param(raw, name, minimum=minimum)


def page_window(args: HandlerArgs) -> tuple[int, int]:
    """``(limit, offset)`` from the query: limit default 100, clamped to the
    documented maximum of 1000 (JUDGMENT on clamping rather than refusing an
    over-large limit -- the docs state the maximum, not the response to
    exceeding it, and clamping is what the core's own paginator does);
    offset default 0."""
    limit = min(_query_int(args, "limit", DEFAULT_LIMIT, minimum=1), MAX_LIMIT)
    offset = _query_int(args, "offset", 0, minimum=0)
    return limit, offset


MAX_EXPANSIONS = 3
""""maximum of three fields per API call" (https://docs.clover.com/dev/docs/expanding-fields)."""


def expansions(args: HandlerArgs, allowed: frozenset[str]) -> frozenset[str]:
    """``expand=a,b,c``: known names only, at most three."""
    raw = args.query("expand")
    if raw is None or not raw.strip():
        return frozenset()
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    # JUDGMENT: a dotted expansion implies its parent -- `lineItems.discounts`
    # alone shows the line items with their discounts. Clover documents the
    # dotted syntax ("one nesting level") and nothing about the parent being
    # absent; implying it is the only reading under which the syntax is
    # usable inside the three-expansion cap, and the implied parent does not
    # count against that cap.
    implied = [name.split(".", 1)[0] for name in wanted if "." in name]
    unknown = [name for name in wanted if name not in allowed]
    if unknown:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Unknown expansion(s): {', '.join(unknown)}.",
            field="expand",
            info={"allowed": sorted(allowed)},
        )
    if len(set(wanted)) > MAX_EXPANSIONS:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"At most {MAX_EXPANSIONS} fields can be expanded per call.",
            field="expand",
            info={"supplied": wanted},
        )
    return frozenset(wanted) | frozenset(implied)


def elements(items: Sequence[dict[str, Any]], hrefs: Sequence[str]) -> dict[str, Any]:
    """Clover's list envelope: ``{"elements": [{"href": ..., ...}, ...]}``.

    The envelope and the per-element ``href`` are documented verbatim
    (https://docs.clover.com/dev/docs/paginating-elements). JUDGMENT that an
    element is the *whole* object plus its href: the example abbreviates
    elements to ``href`` and ``id``, and a list that returned only ids would
    make every consumer fetch each element separately. ``elements`` is always
    present, empty when nothing matched: it is the answer to the request.
    """
    return {"elements": [{"href": href, **item} for href, item in zip(hrefs, items, strict=True)]}
