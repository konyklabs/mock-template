"""The configuration API's thirteen resources: which collection, which
``entityType``, which fields are money.

DOCUMENTED (toast-config-api.yaml v2.5.0): ``GET /config/v2/<resource>`` and
``.../{guid}`` per resource; paging via ``lastModified``/``pageToken`` and the
``Toast-Next-Page-Token`` header, capped at 300 items; archived entities are
never returned. No config resource's MenuItem carries a price.

JUDGMENT: ``lastModified`` compares against an internal ``modified_ms`` Toast
does not document; the seed pins that instant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vendorfake.toast.entities import COL
from vendorfake.toast.model.money import to_dollars

__all__ = ["CONFIG_RESOURCES", "MAX_PAGE", "MODIFIED_KEY", "ConfigResource", "project_config_entity"]

MAX_PAGE = 300
"""``maxItems: 300`` on every list in the specification."""

MODIFIED_KEY = "modified_ms"
"""Internal, stripped on projection; what ``lastModified`` compares against."""

_INTERNAL_KEYS = frozenset({"id", "version", "created_at", "updated_at", MODIFIED_KEY})


@dataclass(frozen=True, slots=True)
class ConfigResource:
    """One ``/config/v2/<segment>``."""

    segment: str
    collection: str
    entity_type: str
    money_keys: tuple[str, ...] = ()


CONFIG_RESOURCES: tuple[ConfigResource, ...] = (
    ConfigResource("diningOptions", COL.dining_options, "DiningOption"),
    ConfigResource("alternatePaymentTypes", COL.alternate_payment_types, "AlternatePaymentType"),
    ConfigResource("taxRates", COL.tax_rates, "TaxRate"),
    ConfigResource("revenueCenters", COL.revenue_centers, "RevenueCenter"),
    ConfigResource("serviceAreas", COL.service_areas, "ServiceArea"),
    ConfigResource("tables", COL.tables, "Table"),
    ConfigResource("restaurantServices", COL.restaurant_services, "RestaurantService"),
    ConfigResource("discounts", COL.discounts, "Discount", ("amount", "fixedTotal")),
    ConfigResource("serviceCharges", COL.service_charges, "ServiceCharge", ("amount",)),
    ConfigResource("menuItems", COL.menu_items, "MenuItem"),
    ConfigResource("menuGroups", COL.menu_groups, "MenuGroup"),
    ConfigResource("menus", COL.config_menus, "Menu"),
    ConfigResource("voidReasons", COL.void_reasons, "VoidReason"),
)
"""The thirteen resources this unit serves, in the order the brief lists them."""


def project_config_entity(resource: ConfigResource, entity: Mapping[str, Any]) -> dict[str, Any]:
    """``{guid, entityType, externalId, ...fields}`` with money in dollars."""
    out: dict[str, Any] = {
        "guid": str(entity["id"]),
        "entityType": resource.entity_type,
        "externalId": entity.get("externalId"),
    }
    for key, value in entity.items():
        if key in _INTERNAL_KEYS or key in ("externalId", "entityType"):
            continue
        if value is None:
            # JUDGMENT: an optional field with no value is omitted, not null
            # -- the specification marks nullable fields with x-nullable.
            continue
        if key in resource.money_keys and isinstance(value, int) and not isinstance(value, bool):
            out[key] = to_dollars(value)
        else:
            out[key] = value
    return out
