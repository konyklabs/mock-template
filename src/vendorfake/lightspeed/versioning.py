"""The retailer-global version counter, and the list envelope built on it.

FOR: the one cross-cutting mechanic every Lightspeed list and every Lightspeed
entity depends on.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/pagination and the
specification's ``Version`` schema):

* "The ``version`` attribute is simply a monotonically increasing integer" --
  ONE sequence per retailer, across every resource type, bumped on every
  mutation of anything. It is not a timestamp and not a page token.
* Every entity carries its ``version``; list responses answer
  ``{"data": [...], "version": {"max": int|null, "min": int|null}}``, with
  both members ``null`` "when the result set is empty".
* Rows come back ascending by version.
* ``after`` is the lower limit and "By default, the value of the ``after``
  parameter will be assumed as equal 0", so the first page of a full sync
  needs no parameter; ``before`` is the upper limit; ``page_size`` caps the
  rows returned; ``deleted`` includes deleted items.
* A caller pages forward with ``after=<the previous response's version.max>``
  and stops when ``data`` comes back empty.

WHY THIS IS IN THE VENDOR PACKAGE. The core has no seam for either half. Its
store keeps a per-entity ``version`` starting at 1 and bumped by one per
update -- optimistic concurrency, a different thing (see ``entities.py``) --
and its :meth:`~vendorfake.core.state.store.Collection.paginate` is an opaque,
fingerprinted, expiring cursor, which is the right model for Square's
``cursor`` and the wrong one for a caller who is expected to read the next
``after`` off the rows themselves. Nothing in ``core/`` was extended for this,
and nothing here reaches into ``core/``'s cursor.

NOT MODELLED, and recorded rather than guessed at: the specification also
defines a ``PaginationMetadata`` schema (``page``/``pageSize``/``results``, a
conventional offset shape). No in-scope list endpoint declares it -- the four
this slice serves declare ``after``/``before``/``page_size``/``deleted`` and
the ``data``+``version`` envelope, checked one by one against the document --
so nothing here implements it. A later slice that models an endpoint declaring
``PaginationMetadata`` adds that shape beside this one; it does not replace it.

DETERMINISM. The counter starts at :data:`FIRST_VERSION` and is re-seeded at
every hydrate, so two units built the same way stamp the same numbers and
``POST /__unit/state/reset`` reproduces them. The starting value is this
project's (JUDGMENT): the real numbers are large and account-historical
(``59780745``, ``1690497245``), and copying one of those would suggest a
provenance the fake does not have.
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
"""The first number the counter hands out. JUDGMENT -- see the module
docstring. Large enough to look like the sequence it imitates, fixed so that
two units agree."""

MAX_PAGE_SIZE = 1000
"""JUDGMENT. The vendor documents ``page_size`` as "The maximum number of
items to be returned in the response" and publishes no ceiling; a request for
more than this is clamped, never refused, which is how the core's own
pagination treats an over-large limit."""

AFTER_PARAM = "after"
BEFORE_PARAM = "before"
PAGE_SIZE_PARAM = "page_size"
DELETED_PARAM = "deleted"


class LightspeedVersions:
    """The retailer's one monotonically increasing version sequence.

    Held on the vendor rather than in the store: it is a counter, not an
    entity, and putting it in the store would make it a row every state digest
    and every journal reader had to know to ignore. :meth:`reset` is called
    from ``hydrate`` alongside the id streams' ``reseed``, which is what makes
    a re-seeded unit stamp the same numbers again.
    """

    __slots__ = ("_next",)

    def __init__(self) -> None:
        self._next = FIRST_VERSION

    def reset(self) -> None:
        """Restart the sequence. Called at hydrate, for the same reason
        :meth:`~vendorfake.core.rand.ids.IdStream.reseed` is."""
        self._next = FIRST_VERSION

    @property
    def current(self) -> int:
        """The last number handed out, or :data:`FIRST_VERSION` - 1 before the
        first draw. Read by tests and by nothing on the wire."""
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
    """``after``/``before``/``page_size``/``deleted``, parsed and validated.

    A non-integer ``after`` is a 422 naming the field rather than a silently
    ignored parameter: the specification types all three as integers, and a
    consumer who sends ``after=abc`` has a bug this fake should show them.
    """

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
    """The four documented list parameters off one request.

    ``after`` defaults to 0, which the pagination page states outright. A
    ``page_size`` at or below zero is refused -- the parameter means "the
    maximum number of items", and zero would name a page nobody asked for --
    and one above :data:`MAX_PAGE_SIZE` is clamped.
    """
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
    """The stored rows this query asks for: filtered, ascending by version, capped.

    ``after`` is exclusive and ``before`` inclusive -- "the lower limit" and
    "the upper limit" for the versions "to be included", read against the
    documented forward-sync pattern, where the next request passes the previous
    response's ``version.max`` as ``after`` and must not receive that row
    again. Making ``after`` inclusive would serve the boundary row twice on
    every page; making ``before`` exclusive would make ``after=x&before=x``
    unable to name a single row. JUDGMENT on both, stated because the docs page
    explicitly does not cover how ``before`` interacts with the walk.
    """
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
    """``{"data": [...], "version": {"max": ..., "min": ...}}``.

    Both members are ``null`` on an empty page, which is what ends a caller's
    walk, and are read off the PROJECTED rows -- the ``version`` a consumer can
    actually see -- rather than off the stored entities, so the envelope and
    the rows can never disagree.
    """
    versions = [int(row["version"]) for row in projected if isinstance(row.get("version"), int)]
    return {
        "data": [dict(row) for row in projected],
        "version": {"max": max(versions) if versions else None, "min": min(versions) if versions else None},
    }


def single(projected: Mapping[str, Any]) -> dict[str, Any]:
    """``{"data": {...}}`` -- the single-record envelope every ``Get*`` and
    every register action answers with (``RetailerResponse``,
    ``OutletResponse``, ``RegisterResponse``,
    ``RegisterPaymentsSummaryResponse``)."""
    return {"data": dict(projected)}
