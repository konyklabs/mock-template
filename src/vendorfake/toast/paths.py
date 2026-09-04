"""Hand-written path constants for Toast, one per route with an operation_id.

FOR: a consumer who wants ``paths.LOGIN`` instead of a route path typed into
every test -- and a route path that CANNOT drift from what the router
actually serves, because ``tests/unit/test_paths_drift.py`` builds a real
Toast vendor, reads its route table and asserts these constants against it:
one constant per non-internal route carrying an ``operation_id``, values
equal, and no constant naming a route that does not exist.

Constants are ``UPPER_SNAKE`` of the route's ``operation_id`` -- the same
identifier :func:`vendorfake.registry.routes` and ``GET /__unit/routes``
publish, including the ``Config<Type>sGet``/``Config<Type>Get`` pairs the
configuration surface generates one per resource
(``vendorfake.toast.model.config.CONFIG_RESOURCES``). Toast's own
``toast/surface/auth.py`` exported ``LOGIN_PATH`` before this module existed;
it is kept as a deprecated alias of :data:`LOGIN` rather than removed, because
v0.1.0 consumers may already import it.

Do not hand-edit a value without also fixing the route it names, or the drift
test fails naming exactly which constant disagrees with the router.
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
"""``POST /orders/v2/applicableDiscounts`` -- ``operation_id="ApplicableDiscounts"``."""
CHECK_DISCOUNTS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/appliedDiscounts"
"""``POST /orders/v2/orders/{guid}/checks/{checkGuid}/appliedDiscounts`` -- ``operation_id="CheckDiscountsPost"``."""
CHECK_PAYMENTS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/payments"
"""``POST /orders/v2/orders/{guid}/checks/{checkGuid}/payments`` -- ``operation_id="CheckPaymentsPost"``."""
CHECK_SELECTIONS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/selections"
"""``POST /orders/v2/orders/{guid}/checks/{checkGuid}/selections`` -- ``operation_id="CheckSelectionsPost"``."""
CONFIG_ALTERNATE_PAYMENT_TYPE_GET = "/config/v2/alternatePaymentTypes/{guid}"
"""``GET /config/v2/alternatePaymentTypes/{guid}`` -- ``operation_id="ConfigAlternatePaymentTypeGet"``."""
CONFIG_ALTERNATE_PAYMENT_TYPES_GET = "/config/v2/alternatePaymentTypes"
"""``GET /config/v2/alternatePaymentTypes`` -- ``operation_id="ConfigAlternatePaymentTypesGet"``."""
CONFIG_DINING_OPTION_GET = "/config/v2/diningOptions/{guid}"
"""``GET /config/v2/diningOptions/{guid}`` -- ``operation_id="ConfigDiningOptionGet"``."""
CONFIG_DINING_OPTIONS_GET = "/config/v2/diningOptions"
"""``GET /config/v2/diningOptions`` -- ``operation_id="ConfigDiningOptionsGet"``."""
CONFIG_DISCOUNT_GET = "/config/v2/discounts/{guid}"
"""``GET /config/v2/discounts/{guid}`` -- ``operation_id="ConfigDiscountGet"``."""
CONFIG_DISCOUNTS_GET = "/config/v2/discounts"
"""``GET /config/v2/discounts`` -- ``operation_id="ConfigDiscountsGet"``."""
CONFIG_MENU_GET = "/config/v2/menus/{guid}"
"""``GET /config/v2/menus/{guid}`` -- ``operation_id="ConfigMenuGet"``."""
CONFIG_MENU_GROUP_GET = "/config/v2/menuGroups/{guid}"
"""``GET /config/v2/menuGroups/{guid}`` -- ``operation_id="ConfigMenuGroupGet"``."""
CONFIG_MENU_GROUPS_GET = "/config/v2/menuGroups"
"""``GET /config/v2/menuGroups`` -- ``operation_id="ConfigMenuGroupsGet"``."""
CONFIG_MENU_ITEM_GET = "/config/v2/menuItems/{guid}"
"""``GET /config/v2/menuItems/{guid}`` -- ``operation_id="ConfigMenuItemGet"``."""
CONFIG_MENU_ITEMS_GET = "/config/v2/menuItems"
"""``GET /config/v2/menuItems`` -- ``operation_id="ConfigMenuItemsGet"``."""
CONFIG_MENUS_GET = "/config/v2/menus"
"""``GET /config/v2/menus`` -- ``operation_id="ConfigMenusGet"``."""
CONFIG_RESTAURANT_SERVICE_GET = "/config/v2/restaurantServices/{guid}"
"""``GET /config/v2/restaurantServices/{guid}`` -- ``operation_id="ConfigRestaurantServiceGet"``."""
CONFIG_RESTAURANT_SERVICES_GET = "/config/v2/restaurantServices"
"""``GET /config/v2/restaurantServices`` -- ``operation_id="ConfigRestaurantServicesGet"``."""
CONFIG_REVENUE_CENTER_GET = "/config/v2/revenueCenters/{guid}"
"""``GET /config/v2/revenueCenters/{guid}`` -- ``operation_id="ConfigRevenueCenterGet"``."""
CONFIG_REVENUE_CENTERS_GET = "/config/v2/revenueCenters"
"""``GET /config/v2/revenueCenters`` -- ``operation_id="ConfigRevenueCentersGet"``."""
CONFIG_SERVICE_AREA_GET = "/config/v2/serviceAreas/{guid}"
"""``GET /config/v2/serviceAreas/{guid}`` -- ``operation_id="ConfigServiceAreaGet"``."""
CONFIG_SERVICE_AREAS_GET = "/config/v2/serviceAreas"
"""``GET /config/v2/serviceAreas`` -- ``operation_id="ConfigServiceAreasGet"``."""
CONFIG_SERVICE_CHARGE_GET = "/config/v2/serviceCharges/{guid}"
"""``GET /config/v2/serviceCharges/{guid}`` -- ``operation_id="ConfigServiceChargeGet"``."""
CONFIG_SERVICE_CHARGES_GET = "/config/v2/serviceCharges"
"""``GET /config/v2/serviceCharges`` -- ``operation_id="ConfigServiceChargesGet"``."""
CONFIG_TABLE_GET = "/config/v2/tables/{guid}"
"""``GET /config/v2/tables/{guid}`` -- ``operation_id="ConfigTableGet"``."""
CONFIG_TABLES_GET = "/config/v2/tables"
"""``GET /config/v2/tables`` -- ``operation_id="ConfigTablesGet"``."""
CONFIG_TAX_RATE_GET = "/config/v2/taxRates/{guid}"
"""``GET /config/v2/taxRates/{guid}`` -- ``operation_id="ConfigTaxRateGet"``."""
CONFIG_TAX_RATES_GET = "/config/v2/taxRates"
"""``GET /config/v2/taxRates`` -- ``operation_id="ConfigTaxRatesGet"``."""
CONFIG_VOID_REASON_GET = "/config/v2/voidReasons/{guid}"
"""``GET /config/v2/voidReasons/{guid}`` -- ``operation_id="ConfigVoidReasonGet"``."""
CONFIG_VOID_REASONS_GET = "/config/v2/voidReasons"
"""``GET /config/v2/voidReasons`` -- ``operation_id="ConfigVoidReasonsGet"``."""
LIST_WEBHOOK_SUBSCRIPTIONS = "/__toast/webhooks/subscriptions"
"""``GET /__toast/webhooks/subscriptions`` -- ``operation_id="ListWebhookSubscriptions"``."""
LOGIN = "/authentication/v1/authentication/login"
"""``POST /authentication/v1/authentication/login`` -- ``operation_id="Login"``."""
MENUS_V3_GET = "/menus/v3/menus"
"""``GET /menus/v3/menus`` -- ``operation_id="MenusV3Get"``."""
MENUS_V3_METADATA_GET = "/menus/v3/metadata"
"""``GET /menus/v3/metadata`` -- ``operation_id="MenusV3MetadataGet"``."""
ORDER_CREATE = "/orders/v2/orders"
"""``POST /orders/v2/orders`` -- ``operation_id="OrderCreate"``."""
ORDER_DELIVERY_INFO_PATCH = "/orders/v2/orders/{guid}/deliveryInfo"
"""``PATCH /orders/v2/orders/{guid}/deliveryInfo`` -- ``operation_id="OrderDeliveryInfoPatch"``."""
ORDER_GET = "/orders/v2/orders/{guid}"
"""``GET /orders/v2/orders/{guid}`` -- ``operation_id="OrderGet"``."""
ORDER_PRICES = "/orders/v2/prices"
"""``POST /orders/v2/prices`` -- ``operation_id="OrderPrices"``."""
ORDER_VOID = "/orders/v2/orders/{guid}/void"
"""``POST /orders/v2/orders/{guid}/void`` -- ``operation_id="OrderVoid"``."""
ORDERS_BULK_GET = "/orders/v2/ordersBulk"
"""``GET /orders/v2/ordersBulk`` -- ``operation_id="OrdersBulkGet"``."""
ORDERS_GET = "/orders/v2/orders"
"""``GET /orders/v2/orders`` -- ``operation_id="OrdersGet"``."""
PARTNERS_CONNECTED_RESTAURANTS_GET = "/partners/v1/connectedRestaurants"
"""``GET /partners/v1/connectedRestaurants`` -- ``operation_id="PartnersConnectedRestaurantsGet"``."""
PARTNERS_RESTAURANTS_GET = "/partners/v1/restaurants"
"""``GET /partners/v1/restaurants`` -- ``operation_id="PartnersRestaurantsGet"``."""
PAYMENT_GET = "/orders/v2/payments/{guid}"
"""``GET /orders/v2/payments/{guid}`` -- ``operation_id="PaymentGet"``."""
PAYMENT_TIP_PATCH = "/orders/v2/orders/{guid}/checks/{checkGuid}/payments/{paymentGuid}"
"""``PATCH /orders/v2/orders/{guid}/checks/{checkGuid}/payments/{paymentGuid}`` -- ``operation_id="PaymentTipPatch"``."""
PAYMENTS_GET = "/orders/v2/payments"
"""``GET /orders/v2/payments`` -- ``operation_id="PaymentsGet"``."""
REGISTER_WEBHOOK_SUBSCRIPTION = "/__toast/webhooks/subscriptions"
"""``POST /__toast/webhooks/subscriptions`` -- ``operation_id="RegisterWebhookSubscription"``."""
REMOVE_WEBHOOK_SUBSCRIPTION = "/__toast/webhooks/subscriptions/{guid}"
"""``DELETE /__toast/webhooks/subscriptions/{guid}`` -- ``operation_id="RemoveWebhookSubscription"``."""
RESTAURANT_GET = "/restaurants/v1/restaurants/{restaurantGUID}"
"""``GET /restaurants/v1/restaurants/{restaurantGUID}`` -- ``operation_id="RestaurantGet"``."""
RESTAURANT_GROUP_RESTAURANTS_GET = "/restaurants/v1/groups/{managementGroupGUID}/restaurants"
"""``GET /restaurants/v1/groups/{managementGroupGUID}/restaurants`` -- ``operation_id="RestaurantGroupRestaurantsGet"``."""
SELECTION_DISCOUNTS_POST = "/orders/v2/orders/{guid}/checks/{checkGuid}/selections/{selectionGuid}/appliedDiscounts"
"""``POST /orders/v2/orders/{guid}/checks/{checkGuid}/selections/{selectionGuid}/appliedDiscounts`` -- ``operation_id="SelectionDiscountsPost"``."""
STOCK_INVENTORY_GET = "/stock/v1/inventory"
"""``GET /stock/v1/inventory`` -- ``operation_id="StockInventoryGet"``."""
STOCK_INVENTORY_SEARCH = "/stock/v1/inventory/search"
"""``POST /stock/v1/inventory/search`` -- ``operation_id="StockInventorySearch"``."""
STOCK_INVENTORY_UPDATE = "/stock/v1/inventory/update"
"""``PUT /stock/v1/inventory/update`` -- ``operation_id="StockInventoryUpdate"``."""
