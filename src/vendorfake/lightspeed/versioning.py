"""The retailer-global version counter and the list envelope built on it.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/pagination and the ``Version``
schema): one monotonically increasing integer per retailer, bumped on every mutation;
lists answer ``{"data": [...], "version": {"max", "min"}}`` ascending by version via
``after``/``before``/``page_size``/``deleted``.

NOT MODELLED: the specification also declares a ``PaginationMetadata`` schema
(``page``/``pageSize``/``results``); no in-scope list endpoint declares it, so it is
unimplemented here rather than silently ignored.

JUDGMENT: lives in the vendor package because the core's own cursor is opaque and
Square-shaped; the counter starts at :data:`FIRST_VERSION`, re-seeded at every hydrate
rather than copying the real vendor's large, account-historical values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from vendorfake.core.kernel.types import HandlerArgs, UnitError, UnitErrorKind
from vendorfake.lightspeed.entities import OBJECT_VERSION

__all__ = [
    "AFTER_PARAM",
    "BEFORE_PARAM",
    "DELETED_PARAM",
    "FIRST_VERSION",
    "MAX_PAGE_SIZE",
    "PAGE_SIZE_PARAM",
    "LightspeedVersions",
    "ListQuery",
    "envelope",
    "read_list_query",
    "single",
    "version_of",
]

FIRST_VERSION = 1_000_000
"""JUDGMENT (see module docstring): large enough to look like the sequence it imitates,
fixed so two units agree."""

MAX_PAGE_SIZE = 1000
"""JUDGMENT: the vendor publishes no ceiling on ``page_size``; an over-large request is
clamped, never refused, matching the core's own pagination."""

AFTER_PARAM = "after"
BEFORE_PARAM = "before"
PAGE_SIZE_PARAM = "page_size"
DELETED_PARAM = "deleted"


class LightspeedVersions:
    """The retailer's one monotonically increasing version sequence; held on the vendor,
    not the store, since it's a counter, not an entity."""

    __slots__ = ("_next",)

    def __init__(self) -> None:
        self._next = FIRST_VERSION

    def reset(self) -> None:
        """Restart the sequence; called at hydrate alongside the id streams' reseed."""
        self._next = FIRST_VERSION

    @property
    def current(self) -> int:
        """The last number handed out, or :data:`FIRST_VERSION` - 1 before the first draw."""
        return self._next - 1

    def bump(self) -> int:
        """The next version. Every mutation of any resource draws one."""
        value = self._next
        self._next += 1
        return value


def version_of(entity: Mapping[str, Any]) -> int:
    """The Lightspeed version stamped on a stored entity, or 0."""
    value = entity.get(OBJECT_VERSION)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class ListQuery:
    """``after``/``before``/``page_size``/``deleted``, parsed and validated; a non-integer
    ``after`` is a 422 naming the field."""

    __slots__ = ("after", "before", "deleted", "page_size")

    def __init__(self, *, after: int, before: int | None, page_size: int | None, deleted: bool) -> None:
        self.after = after
        self.before = before
        self.page_size = page_size
        self.deleted = deleted


def _int_query(args: HandlerArgs, name: str) -> int | None:
    raw = args.query(name)
    if raw is None:
        return None
    text = raw.strip()
    if not text.lstrip("-").isdigit():
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{name} must be an integer version number.",
            field=name,
            info={"supplied": raw},
        )
    return int(text)


def _bool_query(args: HandlerArgs, name: str) -> bool:
    raw = args.query(name)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes"}


def read_list_query(args: HandlerArgs) -> ListQuery:
    """The four documented list parameters off one request; ``after`` defaults to 0,
    ``page_size`` <= 0 is refused, above :data:`MAX_PAGE_SIZE` is clamped."""
    page_size = _int_query(args, PAGE_SIZE_PARAM)
    if page_size is not None:
        if page_size < 1:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{PAGE_SIZE_PARAM} must be 1 or more.",
                field=PAGE_SIZE_PARAM,
                info={"supplied": page_size},
            )
        page_size = min(page_size, MAX_PAGE_SIZE)
    return ListQuery(
        after=_int_query(args, AFTER_PARAM) or 0,
        before=_int_query(args, BEFORE_PARAM),
        page_size=page_size,
        deleted=_bool_query(args, DELETED_PARAM),
    )


def select(rows: Iterable[Mapping[str, Any]], query: ListQuery) -> list[dict[str, Any]]:
    """Filtered, ascending-by-version, capped rows. JUDGMENT: ``after`` is exclusive and
    ``before`` inclusive, matching the documented forward-sync walk."""
    chosen = [
        dict(row)
        for row in rows
        if version_of(row) > query.after
        and (query.before is None or version_of(row) <= query.before)
        and (query.deleted or row.get("deleted_at") is None)
    ]
    chosen.sort(key=version_of)
    if query.page_size is not None:
        chosen = chosen[: query.page_size]
    return chosen


def envelope(projected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``{"data": [...], "version": {"max", "min"}}``, both null when empty; read off the
    projected rows so the envelope and rows never disagree."""
    versions = [int(row["version"]) for row in projected if isinstance(row.get("version"), int)]
    return {
        "data": [dict(row) for row in projected],
        "version": {"max": max(versions) if versions else None, "min": min(versions) if versions else None},
    }


def single(projected: Mapping[str, Any]) -> dict[str, Any]:
    """``{"data": {...}}`` -- the single-record envelope every ``Get*`` and register
    action answers with."""
    return {"data": dict(projected)}
