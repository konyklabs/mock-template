"""What every Clover surface is handed, and the time helpers they share.

``CloverDeps`` is a protocol read live off the vendor rather than copied at
route construction, so a re-``hydrate``d config takes effect immediately.
Entities store epoch milliseconds; the OAuth wire carries Unix seconds
(https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token) --
:func:`wire_seconds` and :func:`is_past_ms` are that conversion.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from vendorfake.clover.config import CloverConfig
from vendorfake.clover.ids import CloverIds
from vendorfake.core.kernel.types import HandlerArgs, UnitContext, UnitError, UnitErrorKind
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
"""DOCUMENTED: "limit" default 100, maximum 1000, with "offset"
(https://docs.clover.com/dev/docs/ordergetorders,
https://docs.clover.com/dev/docs/inventorygetitems)."""


@runtime_checkable
class CloverDeps(Protocol):
    """What a surface needs from the vendor that owns it."""

    @property
    def config(self) -> CloverConfig:
        """Re-read on every access, not cached at route construction."""
        ...

    @property
    def ids(self) -> CloverIds: ...


def is_past_ms(instant_ms: int, clock: Clock) -> bool:
    return instant_ms <= clock.now()


def wire_seconds(instant_ms: int) -> int:
    """An epoch-ms instant as Unix seconds, floored so it never rounds up
    into a second that has not happened."""
    return instant_ms // 1000


def require_merchant(args: HandlerArgs) -> str:
    """The ``{mId}`` of the request, checked against the bearer's merchant.

    JUDGMENT: a mismatched or unknown merchant answers 401, like any other
    authorization failure (https://docs.clover.com/dev/docs/401-unauthorized).
    """
    merchant_id = args.params["mId"]
    principal = args.auth.principal_id if args.auth is not None else None
    if principal != merchant_id:
        raise UnitError(
            UnitErrorKind.UNAUTHORIZED,
            info={"reason": "merchant_mismatch", "path_merchant": merchant_id},
        )
    return merchant_id


def merchant_row(ctx: UnitContext, collection: str, row_id: str, merchant_id: str) -> dict[str, Any] | None:
    """A reference row (employee, order type, customer) of *this* merchant,
    or ``None`` -- JUDGMENT: another merchant's row is treated as absent
    rather than leaking that it exists."""
    row = ctx.store.collection(collection).get(row_id)
    if row is None or row.get("merchant_id") != merchant_id:
        return None
    return dict(row)


def owned_by(merchant_id: str) -> Callable[[Mapping[str, Any]], bool]:
    """The scoping predicate matching :func:`merchant_row`."""
    return lambda row: row.get("merchant_id") == merchant_id


_INTEGER = re.compile(r"^-?\d+$")
"""Refuses ``+5``, ``5x`` and ``1e3``."""


def int_param(raw: str, field: str, *, minimum: int | None = None) -> int:
    """``raw`` as an integer, or a 400 naming ``field``."""
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
    """``(limit, offset)`` from the query: limit default 100, clamped
    (JUDGMENT) to the documented maximum of 1000; offset default 0."""
    limit = min(_query_int(args, "limit", DEFAULT_LIMIT, minimum=1), MAX_LIMIT)
    offset = _query_int(args, "offset", 0, minimum=0)
    return limit, offset


MAX_EXPANSIONS = 3
"""DOCUMENTED: "maximum of three fields per API call"
(https://docs.clover.com/dev/docs/expanding-fields)."""


def expansions(args: HandlerArgs, allowed: frozenset[str]) -> frozenset[str]:
    """``expand=a,b,c``: known names only, at most three. JUDGMENT: a dotted
    expansion implies its parent, which does not count against the cap."""
    raw = args.query("expand")
    if raw is None or not raw.strip():
        return frozenset()
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    implied = [name.split(".", 1)[0] for name in wanted if "." in name]
    unknown = [name for name in wanted if name not in allowed]
    unknown += [name for name in wanted if "." in name and name.split(".", 1)[0] not in allowed and name in allowed]
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
    """DOCUMENTED list envelope ``{"elements": [{"href": ..., ...}, ...]}``
    (https://docs.clover.com/dev/docs/paginating-elements). JUDGMENT that an
    element is the whole object plus its href, not just ``href``/``id``."""
    return {"elements": [{"href": href, **item} for href, item in zip(hrefs, items, strict=True)]}
