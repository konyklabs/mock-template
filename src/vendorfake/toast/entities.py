"""The shapes this vendor stores, and the collections it stores them in.

FOR: giving the surfaces one typed reading of every stored entity the surfaces
*mutate*, so the name of a stored field is written down once. Reference data
(the config lists, the menu, the connected-restaurant rows) is stored as the
plain documents the reference pages list and projected as stored.

INVARIANT: **absence is absence.** A field never set is missing from the
entity dict, never present as ``None``; every ``to_entity`` drops unset
optionals through the core's ``compact()``.

Time, in this package's entities
--------------------------------
Every stored instant is **epoch milliseconds** under the Toast field name it
projects to (``openedDate``, ``modifiedDate``, ...); the projection formats it
as the documented ``...+0000`` string (``model/dates.py``). Internal bookkeeping
fields keep this project's snake_case with a ``_ms`` suffix
(``expires_at_ms``).

Ids: the store needs ``id``; Toast's field is ``guid``. Every entity carries
its guid as ``id`` and the projections spell it ``guid``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact

__all__ = ["COL", "RestaurantEntity", "ToastCollections", "TokenEntity"]


@dataclass(frozen=True, slots=True)
class ToastCollections:
    """The store collections this vendor uses, named once."""

    restaurants: str = "restaurants"
    tokens: str = "tokens"
    orders: str = "orders"
    payments: str = "payments"
    menus: str = "menus"
    stock: str = "stock"
    partners: str = "partners"
    dining_options: str = "dining_options"
    alternate_payment_types: str = "alternate_payment_types"
    tax_rates: str = "tax_rates"
    revenue_centers: str = "revenue_centers"
    service_areas: str = "service_areas"
    tables: str = "tables"
    restaurant_services: str = "restaurant_services"
    discounts: str = "discounts"
    service_charges: str = "service_charges"
    menu_items: str = "menu_items"
    menu_groups: str = "menu_groups"
    config_menus: str = "config_menus"
    void_reasons: str = "void_reasons"

    def names(self) -> tuple[str, ...]:
        return (
            self.restaurants,
            self.tokens,
            self.orders,
            self.payments,
            self.menus,
            self.stock,
            self.partners,
            self.dining_options,
            self.alternate_payment_types,
            self.tax_rates,
            self.revenue_centers,
            self.service_areas,
            self.tables,
            self.restaurant_services,
            self.discounts,
            self.service_charges,
            self.menu_items,
            self.menu_groups,
            self.config_menus,
            self.void_reasons,
        )


COL = ToastCollections()


def _str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _int(value: Any, default: int = 0) -> int:
    return default if not isinstance(value, int) or isinstance(value, bool) else value


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value)
    return ()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class RestaurantEntity:
    """One restaurant, stored in the documented restaurants-API shape
    (https://doc.toasttab.com/toast-api-specifications/toast-restaurants-api.yaml):
    ``general{name, locationName, locationCode, description, timeZone,
    closeoutHour, managementGroupGuid, currencyCode}`` and ``location{...}``;
    the other documented blocks (``urls``, ``schedules``, ``delivery``,
    ``onlineOrdering``, ``prepTimes``) are stored as the seed supplied them --
    their sub-shapes are not enumerated anywhere (audit gap 11)."""

    id: str
    general: dict[str, Any]
    location: dict[str, Any]
    urls: dict[str, Any]
    schedules: dict[str, Any]
    delivery: dict[str, Any]
    onlineOrdering: dict[str, Any]
    prepTimes: dict[str, Any]

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> RestaurantEntity:
        return cls(
            id=_str(entity["id"]),
            general=_mapping(entity.get("general")),
            location=_mapping(entity.get("location")),
            urls=_mapping(entity.get("urls")),
            schedules=_mapping(entity.get("schedules")),
            delivery=_mapping(entity.get("delivery")),
            onlineOrdering=_mapping(entity.get("onlineOrdering")),
            prepTimes=_mapping(entity.get("prepTimes")),
        )

    def to_entity(self) -> Entity:
        return {
            "id": self.id,
            "general": dict(self.general),
            "location": dict(self.location),
            "urls": dict(self.urls),
            "schedules": dict(self.schedules),
            "delivery": dict(self.delivery),
            "onlineOrdering": dict(self.onlineOrdering),
            "prepTimes": dict(self.prepTimes),
        }

    @property
    def name(self) -> str:
        return _str(self.general.get("name"))

    @property
    def time_zone(self) -> str:
        return _str(self.general.get("timeZone"), "UTC")

    @property
    def closeout_hour(self) -> int:
        return _int(self.general.get("closeoutHour"))

    @property
    def currency_code(self) -> str:
        return _str(self.general.get("currencyCode"), "USD")

    @property
    def management_group_guid(self) -> str | None:
        value = self.general.get("managementGroupGuid")
        return None if value is None else str(value)

    def wire(self) -> dict[str, Any]:
        """The documented restaurant document, ``guid`` first."""
        return {
            "guid": self.id,
            "general": dict(self.general),
            "urls": dict(self.urls),
            "location": dict(self.location),
            "schedules": dict(self.schedules),
            "delivery": dict(self.delivery),
            "onlineOrdering": dict(self.onlineOrdering),
            "prepTimes": dict(self.prepTimes),
        }


@dataclass(frozen=True, slots=True)
class TokenEntity:
    """One issued access token. ``id`` is the JWT's ``jti``.

    No refresh token: Toast documents none a client may use. ``expires_at_ms``
    is the instant the token stops working -- at or before is expired.
    """

    id: str
    access_token: str
    client_id: str
    partner_guid: str
    expires_at_ms: int
    scopes: tuple[str, ...] = ()
    createdDate: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> TokenEntity:
        return cls(
            id=_str(entity["id"]),
            access_token=_str(entity.get("access_token")),
            client_id=_str(entity.get("client_id")),
            partner_guid=_str(entity.get("partner_guid")),
            expires_at_ms=_int(entity.get("expires_at_ms")),
            scopes=_str_tuple(entity.get("scopes")),
            createdDate=_opt_int(entity.get("createdDate")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "access_token": self.access_token,
                "client_id": self.client_id,
                "partner_guid": self.partner_guid,
                "expires_at_ms": self.expires_at_ms,
                "scopes": list(self.scopes),
                "createdDate": self.createdDate,
            }
        )
