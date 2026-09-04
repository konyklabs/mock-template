"""The Outlets tag: the version-cursor list, and one outlet by id.

DOCUMENTED (two operations, both ``🔒 Requires: `outlets:read` scope``):

* ``GET /outlets`` (``ListOutlets``) takes ``after``, ``before``,
  ``page_size`` and ``deleted`` and answers ``OutletCollection`` --
  ``{"data": [...], "version": {"max": ..., "min": ...}}``, ascending by
  version;
* ``GET /outlets/{outlet_id}`` (``GetOutletByID``) answers
  ``OutletResponse`` -- ``{"data": {...}}``.

The tag is read-only in this specification version: there is no create, update
or delete for an outlet anywhere in the 135 paths, which is why nothing here
mutates.

THE PAGINATION DECLARATION on the list route is what lets a language-independent
conformance check walk it. It is declared ``style="cursor"`` because that is
the shape the core's :class:`~vendorfake.core.kernel.types.PaginationSpec`
offers that matches how a caller actually pages here -- read the next cursor
off the response and send it back -- even though the "cursor" is a plain
integer version rather than an opaque token: ``cursor_param="after"``,
``next_cursor_path="version.max"``, ``limit_param="page_size"``. The walk ends
on an empty page, where ``version.max`` is ``null`` and the check stops.
"""

from __future__ import annotations

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, PaginationSpec, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.lightspeed.config import SCOPE_OUTLETS_READ
from vendorfake.lightspeed.entities import COL
from vendorfake.lightspeed.model.retailer import project_outlet
from vendorfake.lightspeed.paths import GET_OUTLET_BY_ID, LIST_OUTLETS
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps
from vendorfake.lightspeed.versioning import (
    AFTER_PARAM,
    PAGE_SIZE_PARAM,
    envelope,
    read_list_query,
    select,
    single,
)

__all__ = ["CAPABILITY", "VERSION_CURSOR_PAGINATION", "LightspeedOutletsSurface", "outlet_routes"]

CAPABILITY = "outlets"

VERSION_CURSOR_PAGINATION = PaginationSpec(
    style="cursor",
    items_path="data",
    where="query",
    limit_param=PAGE_SIZE_PARAM,
    cursor_param=AFTER_PARAM,
    next_cursor_path="version.max",
    id_path="id",
)
"""The one pagination shape every version-cursor list in this package
declares. Shared from here rather than repeated per surface, so the four
parameter names are written once."""


class LightspeedOutletsSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path=LIST_OUTLETS,
                capability=CAPABILITY,
                handler=self.list_outlets,
                auth=BEARER_AUTH,
                scopes=(SCOPE_OUTLETS_READ,),
                pagination=VERSION_CURSOR_PAGINATION,
                operation_id="ListOutlets",
                summary="Outlets, ascending by version; after/before/page_size/deleted.",
            ),
            Route(
                method="GET",
                path=GET_OUTLET_BY_ID,
                capability=CAPABILITY,
                handler=self.get_outlet,
                auth=BEARER_AUTH,
                scopes=(SCOPE_OUTLETS_READ,),
                operation_id="GetOutletByID",
                summary="One outlet by id.",
            ),
        )

    def list_outlets(self, args: HandlerArgs) -> ReplyInit:
        query = read_list_query(args)
        rows = select(args.ctx.store.collection(COL.outlets).all(), query)
        return json_(envelope([project_outlet(row) for row in rows]))

    def get_outlet(self, args: HandlerArgs) -> ReplyInit:
        outlet_id = args.params["outlet_id"]
        stored = args.ctx.store.collection(COL.outlets).get(outlet_id)
        if stored is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Outlet {outlet_id} was not found.", field="outlet_id")
        return json_(single(project_outlet(stored)))


def outlet_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedOutletsSurface(deps).routes()
