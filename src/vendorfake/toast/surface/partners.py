"""The partners API surface: which restaurants are connected to this client.

DOCUMENTED (toast-partners-api.yaml v1.0.2,
apiPartnersGettingAccessibleRestaurants.html) -- partner accounts only:

=====================  ==================================================
ConnectedRestaurants   ``GET /partners/v1/connectedRestaurants?lastModified&pageSize&pageToken``
Restaurants            ``GET /partners/v1/restaurants?lastModified``
=====================  ==================================================

The first answers the documented page envelope (``model/partners.py``), the
second the bare array. 403 "insufficient permissions" is the kernel's scope
check on ``partners:read``. Neither takes ``Toast-Restaurant-External-ID``:
these are the routes a partner calls to learn which restaurants it may name
in that header.

JUDGMENT: ``lastModified`` compares against the row's epoch-ms
``modifiedDate`` inclusively; ``pageSize`` above 200 is refused rather than
clamped (the specification states a maximum and this unit says so); the page
token is the core's opaque cursor.
"""

from __future__ import annotations

from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, PaginationSpec, ReplyInit, Route
from vendorfake.toast.entities import COL
from vendorfake.toast.model.dates import parse_rest_date
from vendorfake.toast.model.partners import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    page_envelope,
    page_token,
    parse_page_token,
    project_connected_restaurant,
)
from vendorfake.toast.surface.common import BEARER_AUTH, ToastDeps, int_param

__all__ = ["CAPABILITY", "ToastPartnersSurface", "partner_routes"]

CAPABILITY = "partners"


class ToastPartnersSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/partners/v1/connectedRestaurants",
                capability=CAPABILITY,
                handler=self.connected_restaurants,
                auth=BEARER_AUTH,
                scopes=("partners:read",),
                operation_id="PartnersConnectedRestaurantsGet",
                summary="The restaurants connected to this partner, in the documented page envelope.",
                pagination=PaginationSpec(
                    style="cursor",
                    items_path="results",
                    limit_param="pageSize",
                    cursor_param="pageToken",
                    next_cursor_path="nextPageToken",
                    id_path="restaurantGuid",
                    walkable=False,
                    unwalkable_reason=(
                        "The seed document models exactly one restaurant (seed/document.py declares "
                        "`restaurant` as a single object, not a list) and the partners row is "
                        "derived from it (seed/hydrate.py::_insert_partner), so this listing cannot "
                        "hold the two rows a page-boundary walk needs without a second restaurant "
                        "-- a unit-wide scope decision, weighed on konyklabs/roadmap#47 rather than "
                        "changed here."
                    ),
                ),
            ),
            Route(
                method="GET",
                path="/partners/v1/restaurants",
                capability=CAPABILITY,
                handler=self.restaurants,
                auth=BEARER_AUTH,
                scopes=("partners:read",),
                operation_id="PartnersRestaurantsGet",
                summary="The same rows as a bare array.",
            ),
        )

    def _rows(self, args: HandlerArgs) -> list[dict[str, object]]:
        raw = args.query("lastModified")
        since = None if raw is None else parse_rest_date(raw, field="lastModified")
        scopes = self._deps.config.scopes
        return [
            project_connected_restaurant(row, scopes)
            for row in args.ctx.store.collection(COL.partners).all()
            if since is None or int(row.get("modifiedDate", 0)) >= since
        ]

    def connected_restaurants(self, args: HandlerArgs) -> ReplyInit:
        raw_size = args.query("pageSize")
        page_size = (
            DEFAULT_PAGE_SIZE if raw_size is None else int_param(raw_size, "pageSize", minimum=1, maximum=MAX_PAGE_SIZE)
        )
        rows = self._rows(args)
        token = args.query("pageToken")
        # JUDGMENT on the format, DOCUMENTED on the shape: the guide's page
        # tokens are base64 of ``p=<page>,s:<size>`` (``cD0xLHM6MTAw`` on its
        # first page). The unit mints and accepts exactly that, so the token
        # it answers is one a consumer can send back -- a deep-lens finding on
        # roadmap#56 caught the earlier null/opaque-cursor mismatch.
        page_number = 1 if token is None else parse_page_token(token, page_size=page_size)
        start = (page_number - 1) * page_size
        items = rows[start : start + page_size]
        has_more = start + page_size < len(rows)
        return json_(
            _omit_none(
                page_envelope(
                    items,
                    total=len(rows),
                    page_size=page_size,
                    page_number=page_number,
                    current_token=page_token(page_number, page_size),
                    next_token=page_token(page_number + 1, page_size) if has_more else None,
                )
            )
        )

    def restaurants(self, args: HandlerArgs) -> ReplyInit:
        return json_(_omit_none(self._rows(args)))


def _omit_none(node: Any) -> Any:
    """JUDGMENT: a page token or a restaurant field with no value is omitted,
    not answered null -- the partners specification types them as plain
    strings; the guide documents null only for ``nextPageNum`` and
    ``previousPageNum`` (declared deviations). Found by the fidelity
    validator (konyklabs/roadmap#56)."""
    if isinstance(node, dict):
        return {k: _omit_none(v) for k, v in node.items() if v is not None or k in ("nextPageNum", "previousPageNum")}
    if isinstance(node, list):
        return [_omit_none(item) for item in node]
    return node


def partner_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastPartnersSurface(deps).routes()
