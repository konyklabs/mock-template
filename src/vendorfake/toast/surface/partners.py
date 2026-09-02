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
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route
from vendorfake.toast.entities import COL
from vendorfake.toast.model.dates import parse_rest_date
from vendorfake.toast.model.partners import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    page_envelope,
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
        page = args.ctx.store.collection(COL.partners).paginate(
            rows,
            limit=page_size,
            cursor=token,
            fingerprint={
                "resource": "connectedRestaurants",
                "pageSize": page_size,
                "lastModified": args.query("lastModified"),
            },
            max_limit=MAX_PAGE_SIZE,
            default_limit=DEFAULT_PAGE_SIZE,
        )
        # The page number is derived from how many rows precede this page; the
        # core's cursor carries the offset but does not expose it, so it is
        # recomputed from the first row's position.
        first_index = rows.index(page.items[0]) if page.items else len(rows)
        return json_(
            _omit_none(
                page_envelope(
                    page.items,
                    total=len(rows),
                    page_size=page_size,
                    page_number=first_index // page_size + 1,
                    current_token=token,
                    next_token=page.cursor,
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
