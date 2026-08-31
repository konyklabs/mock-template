"""The restaurants API surface: one restaurant, and a management group's restaurants.

DOCUMENTED (toast-restaurants-api.yaml v1.0.0):

============  =============================================================
GetRestaurant ``GET /restaurants/v1/restaurants/{restaurantGUID}``
GetGroup      ``GET /restaurants/v1/groups/{managementGroupGUID}/restaurants?includeArchived``
============  =============================================================

The restaurant document is ``{guid, general{...}, urls{}, location{...},
schedules{}, delivery{}, onlineOrdering{}, prepTimes{}}``; the specification
gives no example values (audit gap 11), so the seed supplies the documented
``general`` and ``location`` fields and the other blocks as it chooses.

JUDGMENT: both routes take a bearer and no ``Toast-Restaurant-External-ID`` --
the guid is in the path, and the group route is about several restaurants at
once. The group answer is the array of full restaurant documents (the
specification names an array and not its element shape). ``includeArchived``
is accepted and changes nothing: nothing here archives.
"""

from __future__ import annotations

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.toast.entities import COL, RestaurantEntity
from vendorfake.toast.surface.common import BEARER_AUTH, ToastDeps

__all__ = ["CAPABILITY", "ToastRestaurantsSurface", "restaurant_routes"]

CAPABILITY = "restaurants"


class ToastRestaurantsSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/restaurants/v1/restaurants/{restaurantGUID}",
                capability=CAPABILITY,
                handler=self.get_restaurant,
                auth=BEARER_AUTH,
                scopes=("restaurants:read",),
                operation_id="RestaurantGet",
                summary="One restaurant: general, location, urls, schedules, delivery, onlineOrdering, prepTimes.",
            ),
            Route(
                method="GET",
                path="/restaurants/v1/groups/{managementGroupGUID}/restaurants",
                capability=CAPABILITY,
                handler=self.get_group_restaurants,
                auth=BEARER_AUTH,
                scopes=("restaurants:read",),
                operation_id="RestaurantGroupRestaurantsGet",
                summary="Every restaurant in a management group.",
            ),
        )

    def get_restaurant(self, args: HandlerArgs) -> ReplyInit:
        guid = args.params["restaurantGUID"]
        stored = args.ctx.store.collection(COL.restaurants).get(guid)
        if stored is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Restaurant {guid} was not found.", field="restaurantGUID")
        return json_(RestaurantEntity.from_entity(stored).wire())

    def get_group_restaurants(self, args: HandlerArgs) -> ReplyInit:
        group = args.params["managementGroupGUID"]
        members = [
            RestaurantEntity.from_entity(row)
            for row in args.ctx.store.collection(COL.restaurants).all()
            if RestaurantEntity.from_entity(row).management_group_guid == group
        ]
        if not members:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Management group {group} was not found.",
                field="managementGroupGUID",
            )
        return json_([member.wire() for member in members])


def restaurant_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastRestaurantsSurface(deps).routes()
