"""Hand-written path constants for Toast, one per route with an operation_id.

Constants are ``UPPER_SNAKE`` of the route's ``operation_id`` -- the same
identifier :func:`vendorfake.registry.routes` and ``GET /__unit/routes``
publish. ``tests/unit/test_paths_drift.py`` builds a real Toast vendor and
asserts every constant here against its route table, so a hand-edited value
that disagrees with the router fails that test by name.
"""

from __future__ import annotations

__all__ = [
    "APPLICABLE_DISCOUNTS",
    "CHECK_DISCOUNTS_POST",
    "CHECK_PAYMENTS_POST",
    "CHECK_SELECTIONS_POST",
    "CONFIG_ALTERNATE_PAYMENT_TYPES_GET",
    "CONFIG_ALTERNATE_PAYMENT_TYPE_GET",
    "CONFIG_DINING_OPTIONS_GET",
    "CONFIG_DINING_OPTION_GET",
    "CONFIG_DISCOUNTS_GET",
    "CONFIG_DISCOUNT_GET",
    "CONFIG_MENUS_GET",
    "CONFIG_MENU_GET",
    "CONFIG_MENU_GROUPS_GET",
    "CONFIG_MENU_GROUP_GET",
    "CONFIG_MENU_ITEMS_GET",
    "CONFIG_MENU_ITEM_GET",
    "CONFIG_RESTAURANT_SERVICES_GET",
    "CONFIG_RESTAURANT_SERVICE_GET",
    "CONFIG_REVENUE_CENTERS_GET",
    "CONFIG_REVENUE_CENTER_GET",
    "CONFIG_SERVICE_AREAS_GET",
    "CONFIG_SERVICE_AREA_GET",
    "CONFIG_SERVICE_CHARGES_GET",
    "CONFIG_SERVICE_CHARGE_GET",
    "CONFIG_TABLES_GET",
    "CONFIG_TABLE_GET",
    "CONFIG_TAX_RATES_GET",
    "CONFIG_TAX_RATE_GET",
    "CONFIG_VOID_REASONS_GET",
    "CONFIG_VOID_REASON_GET",
    "LIST_WEBHOOK_SUBSCRIPTIONS",
    "LOGIN",
    "MENUS_V3_GET",
    "MENUS_V3_METADATA_GET",
    "ORDERS_BULK_GET",
    "ORDERS_GET",
    "ORDER_CREATE",
    "ORDER_DELIVERY_INFO_PATCH",
    "ORDER_GET",
    "ORDER_PRICES",
    "ORDER_VOID",
    "PARTNERS_CONNECTED_RESTAURANTS_GET",
    "PARTNERS_RESTAURANTS_GET",
    "PAYMENTS_GET",
    "PAYMENT_GET",
    "PAYMENT_TIP_PATCH",
    "REGISTER_WEBHOOK_SUBSCRIPTION",
    "REMOVE_WEBHOOK_SUBSCRIPTION",
    "RESTAURANT_GET",
    "RESTAURANT_GROUP_RESTAURANTS_GET",
    "SELECTION_DISCOUNTS_POST",
    "STOCK_INVENTORY_GET",
    "STOCK_INVENTORY_SEARCH",
    "STOCK_INVENTORY_UPDATE",
]

APPLICABLE_DISCOUNTS = "/orders/v2/applicableDiscounts"
CHECK_DISCOUNTS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/appliedDiscounts"
CHECK_PAYMENTS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/payments"
CHECK_SELECTIONS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/selections"
CONFIG_ALTERNATE_PAYMENT_TYPE_GET = "/config/v2/alternatePaymentTypes/{guid}"
CONFIG_ALTERNATE_PAYMENT_TYPES_GET = "/config/v2/alternatePaymentTypes"
CONFIG_DINING_OPTION_GET = "/config/v2/diningOptions/{guid}"
CONFIG_DINING_OPTIONS_GET = "/config/v2/diningOptions"
CONFIG_DISCOUNT_GET = "/config/v2/discounts/{guid}"
CONFIG_DISCOUNTS_GET = "/config/v2/discounts"
CONFIG_MENU_GET = "/config/v2/menus/{guid}"
CONFIG_MENU_GROUP_GET = "/config/v2/menuGroups/{guid}"
CONFIG_MENU_GROUPS_GET = "/config/v2/menuGroups"
CONFIG_MENU_ITEM_GET = "/config/v2/menuItems/{guid}"
CONFIG_MENU_ITEMS_GET = "/config/v2/menuItems"
CONFIG_MENUS_GET = "/config/v2/menus"
CONFIG_RESTAURANT_SERVICE_GET = "/config/v2/restaurantServices/{guid}"
CONFIG_RESTAURANT_SERVICES_GET = "/config/v2/restaurantServices"
CONFIG_REVENUE_CENTER_GET = "/config/v2/revenueCenters/{guid}"
CONFIG_REVENUE_CENTERS_GET = "/config/v2/revenueCenters"
CONFIG_SERVICE_AREA_GET = "/config/v2/serviceAreas/{guid}"
CONFIG_SERVICE_AREAS_GET = "/config/v2/serviceAreas"
CONFIG_SERVICE_CHARGE_GET = "/config/v2/serviceCharges/{guid}"
CONFIG_SERVICE_CHARGES_GET = "/config/v2/serviceCharges"
CONFIG_TABLE_GET = "/config/v2/tables/{guid}"
CONFIG_TABLES_GET = "/config/v2/tables"
CONFIG_TAX_RATE_GET = "/config/v2/taxRates/{guid}"
CONFIG_TAX_RATES_GET = "/config/v2/taxRates"
CONFIG_VOID_REASON_GET = "/config/v2/voidReasons/{guid}"
CONFIG_VOID_REASONS_GET = "/config/v2/voidReasons"
LIST_WEBHOOK_SUBSCRIPTIONS = "/__toast/webhooks/subscriptions"
LOGIN = "/authentication/v1/authentication/login"
MENUS_V3_GET = "/menus/v3/menus"
MENUS_V3_METADATA_GET = "/menus/v3/metadata"
ORDER_CREATE = "/orders/v2/orders"
ORDER_DELIVERY_INFO_PATCH = "/orders/v2/orders/{guid}/deliveryInfo"
ORDER_GET = "/orders/v2/orders/{guid}"
ORDER_PRICES = "/orders/v2/prices"
ORDER_VOID = "/orders/v2/orders/{guid}/void"
ORDERS_BULK_GET = "/orders/v2/ordersBulk"
ORDERS_GET = "/orders/v2/orders"
PARTNERS_CONNECTED_RESTAURANTS_GET = "/partners/v1/connectedRestaurants"
PARTNERS_RESTAURANTS_GET = "/partners/v1/restaurants"
PAYMENT_GET = "/orders/v2/payments/{guid}"
PAYMENT_TIP_PATCH = "/orders/v2/orders/{guid}/checks/{checkGuid}/payments/{paymentGuid}"
PAYMENTS_GET = "/orders/v2/payments"
REGISTER_WEBHOOK_SUBSCRIPTION = "/__toast/webhooks/subscriptions"
REMOVE_WEBHOOK_SUBSCRIPTION = "/__toast/webhooks/subscriptions/{guid}"
RESTAURANT_GET = "/restaurants/v1/restaurants/{restaurantGUID}"
RESTAURANT_GROUP_RESTAURANTS_GET = "/restaurants/v1/groups/{managementGroupGUID}/restaurants"
SELECTION_DISCOUNTS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/selections/{selectionGuid}/appliedDiscounts"
STOCK_INVENTORY_GET = "/stock/v1/inventory"
STOCK_INVENTORY_SEARCH = "/stock/v1/inventory/search"
STOCK_INVENTORY_UPDATE = "/stock/v1/inventory/update"
