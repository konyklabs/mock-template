"""The Menus API V3 surface: the published menu and its metadata.

DOCUMENTED (toast-menus-api-v3.yaml v3.4.1, apiMenusV3.html):

=========  =====================================================
GetMenus   ``GET /menus/v3/menus`` -- the whole document
GetMeta    ``GET /menus/v3/metadata`` -- ``{restaurantGuid, lastUpdated}``
=========  =====================================================

Both take no query parameters, both require ``Toast-Restaurant-External-ID``,
and both 404 with the message this module exposes as ``NO_PUBLISHED_DATA``
when nothing is published. Ordering integrations are steered toward this V3
surface; V2 is deliberately not served (``TOAST_NOT_MODELED``). The documented
per-location throttle -- capped at once a second -- is not accounted.
"""

from __future__ import annotations

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.state.store import Entity
from vendorfake.toast.entities import COL
from vendorfake.toast.model.menus import project_menu_metadata, project_menu_v3
from vendorfake.toast.surface.common import RESTAURANT_AUTH, ToastDeps, require_restaurant

__all__ = ["CAPABILITY", "NO_PUBLISHED_DATA", "ToastMenusSurface", "menu_routes", "published_menu"]

CAPABILITY = "menus"

NO_PUBLISHED_DATA = "No published data was found for the restaurant."
"""The documented 404 phrase."""


class ToastMenusSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/menus/v3/menus",
                capability=CAPABILITY,
                handler=self.get_menus,
                auth=RESTAURANT_AUTH,
                scopes=("menus:read",),
                operation_id="MenusV3Get",
                summary="The published V3 menu document: menus, groups, items and the three reference maps.",
            ),
            Route(
                method="GET",
                path="/menus/v3/metadata",
                capability=CAPABILITY,
                handler=self.get_metadata,
                auth=RESTAURANT_AUTH,
                scopes=("menus:read",),
                operation_id="MenusV3MetadataGet",
                summary="{restaurantGuid, lastUpdated} -- poll this before re-fetching the menu.",
            ),
        )

    def get_menus(self, args: HandlerArgs) -> ReplyInit:
        restaurant = require_restaurant(args)
        return json_(project_menu_v3(published_menu(args), time_zone=restaurant.time_zone))

    def get_metadata(self, args: HandlerArgs) -> ReplyInit:
        require_restaurant(args)
        return json_(project_menu_metadata(published_menu(args)))


def published_menu(args: HandlerArgs) -> Entity:
    """The restaurant's stored V3 document, or the documented 404."""
    restaurant = require_restaurant(args)
    stored = args.ctx.store.collection(COL.menus).get(restaurant.id)
    if stored is None:
        raise UnitError(UnitErrorKind.NOT_FOUND, detail=NO_PUBLISHED_DATA, info={"restaurantGuid": restaurant.id})
    return stored


def menu_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastMenusSurface(deps).routes()
