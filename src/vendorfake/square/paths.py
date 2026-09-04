"""Hand-written path constants for Square, one per route with an operation_id.

FOR: a consumer who wants ``paths.OBTAIN_TOKEN`` instead of a route path typed
into every test -- and a route path that CANNOT drift from what the router
actually serves, because ``tests/unit/test_paths_drift.py`` builds a real
Square vendor, reads its route table and asserts these constants against it:
one constant per non-internal route carrying an ``operation_id``, values
equal, and no constant naming a route that does not exist.

Constants are ``UPPER_SNAKE`` of the route's ``operation_id`` -- the same
identifier :func:`vendorfake.registry.routes` and ``GET /__unit/routes``
publish, so a name here is always the name a consumer already has from
discovering the surface at runtime. Path templates keep the brace form
(``{order_id}``), matching every other place a template is written in this
project.

Do not hand-edit a value without also fixing the route it names, or the drift
test fails naming exactly which constant disagrees with the router -- that is
the point of the test existing at all.
"""

from __future__ import annotations

__all__ = [
    "ACCUMULATE_LOYALTY_POINTS",
    "AUTHORIZE",
    "BATCH_CHANGE_INVENTORY",
    "BATCH_RETRIEVE_INVENTORY_COUNTS",
    "BATCH_RETRIEVE_ORDERS",
    "CANCEL_PAYMENT",
    "COMPLETE_PAYMENT",
    "CREATE_LOYALTY_ACCOUNT",
    "CREATE_ORDER",
    "CREATE_ORDER_AT_LOCATION",
    "CREATE_PAYMENT",
    "CREATE_WEBHOOK_SUBSCRIPTION",
    "DELETE_WEBHOOK_SUBSCRIPTION",
    "GET_PAYMENT",
    "LIST_CATALOG",
    "LIST_LOCATIONS",
    "LIST_MERCHANTS",
    "LIST_WEBHOOK_EVENT_TYPES",
    "LIST_WEBHOOK_SUBSCRIPTIONS",
    "OBTAIN_TOKEN",
    "PAY_ORDER",
    "RETRIEVE_CATALOG_OBJECT",
    "RETRIEVE_INVENTORY_COUNT",
    "RETRIEVE_LOYALTY_PROGRAM",
    "RETRIEVE_MERCHANT",
    "RETRIEVE_ORDER",
    "RETRIEVE_TOKEN_STATUS",
    "RETRIEVE_WEBHOOK_SUBSCRIPTION",
    "REVOKE_TOKEN",
    "SEARCH_CATALOG_OBJECTS",
    "SEARCH_LOYALTY_ACCOUNTS",
    "SEARCH_ORDERS",
    "TEST_WEBHOOK_SUBSCRIPTION",
    "UPDATE_ORDER",
    "UPSERT_CATALOG_OBJECT",
]

ACCUMULATE_LOYALTY_POINTS = "/v2/loyalty/accounts/{account_id}/accumulate"
"""``POST /v2/loyalty/accounts/{account_id}/accumulate`` -- ``operation_id="AccumulateLoyaltyPoints"``."""
AUTHORIZE = "/oauth2/authorize"
"""``GET /oauth2/authorize`` -- ``operation_id="Authorize"``."""
BATCH_CHANGE_INVENTORY = "/v2/inventory/changes/batch-create"
"""``POST /v2/inventory/changes/batch-create`` -- ``operation_id="BatchChangeInventory"``."""
BATCH_RETRIEVE_INVENTORY_COUNTS = "/v2/inventory/counts/batch-retrieve"
"""``POST /v2/inventory/counts/batch-retrieve`` -- ``operation_id="BatchRetrieveInventoryCounts"``."""
BATCH_RETRIEVE_ORDERS = "/v2/orders/batch-retrieve"
"""``POST /v2/orders/batch-retrieve`` -- ``operation_id="BatchRetrieveOrders"``."""
CANCEL_PAYMENT = "/v2/payments/{payment_id}/cancel"
"""``POST /v2/payments/{payment_id}/cancel`` -- ``operation_id="CancelPayment"``."""
COMPLETE_PAYMENT = "/v2/payments/{payment_id}/complete"
"""``POST /v2/payments/{payment_id}/complete`` -- ``operation_id="CompletePayment"``."""
CREATE_LOYALTY_ACCOUNT = "/v2/loyalty/accounts"
"""``POST /v2/loyalty/accounts`` -- ``operation_id="CreateLoyaltyAccount"``."""
CREATE_ORDER = "/v2/orders"
"""``POST /v2/orders`` -- ``operation_id="CreateOrder"``."""
CREATE_ORDER_AT_LOCATION = "/v2/locations/{location_id}/orders"
"""``POST /v2/locations/{location_id}/orders`` -- ``operation_id="CreateOrderAtLocation"``."""
CREATE_PAYMENT = "/v2/payments"
"""``POST /v2/payments`` -- ``operation_id="CreatePayment"``."""
CREATE_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions"
"""``POST /v2/webhooks/subscriptions`` -- ``operation_id="CreateWebhookSubscription"``."""
DELETE_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions/{subscription_id}"
"""``DELETE /v2/webhooks/subscriptions/{subscription_id}`` -- ``operation_id="DeleteWebhookSubscription"``."""
GET_PAYMENT = "/v2/payments/{payment_id}"
"""``GET /v2/payments/{payment_id}`` -- ``operation_id="GetPayment"``."""
LIST_CATALOG = "/v2/catalog/list"
"""``GET /v2/catalog/list`` -- ``operation_id="ListCatalog"``."""
LIST_LOCATIONS = "/v2/locations"
"""``GET /v2/locations`` -- ``operation_id="ListLocations"``."""
LIST_MERCHANTS = "/v2/merchants"
"""``GET /v2/merchants`` -- ``operation_id="ListMerchants"``."""
LIST_WEBHOOK_EVENT_TYPES = "/v2/webhooks/event-types"
"""``GET /v2/webhooks/event-types`` -- ``operation_id="ListWebhookEventTypes"``."""
LIST_WEBHOOK_SUBSCRIPTIONS = "/v2/webhooks/subscriptions"
"""``GET /v2/webhooks/subscriptions`` -- ``operation_id="ListWebhookSubscriptions"``."""
OBTAIN_TOKEN = "/oauth2/token"
"""``POST /oauth2/token`` -- ``operation_id="ObtainToken"``."""
PAY_ORDER = "/v2/orders/{order_id}/pay"
"""``POST /v2/orders/{order_id}/pay`` -- ``operation_id="PayOrder"``."""
RETRIEVE_CATALOG_OBJECT = "/v2/catalog/object/{object_id}"
"""``GET /v2/catalog/object/{object_id}`` -- ``operation_id="RetrieveCatalogObject"``."""
RETRIEVE_INVENTORY_COUNT = "/v2/inventory/{catalog_object_id}"
"""``GET /v2/inventory/{catalog_object_id}`` -- ``operation_id="RetrieveInventoryCount"``."""
RETRIEVE_LOYALTY_PROGRAM = "/v2/loyalty/programs/{program_id}"
"""``GET /v2/loyalty/programs/{program_id}`` -- ``operation_id="RetrieveLoyaltyProgram"``."""
RETRIEVE_MERCHANT = "/v2/merchants/{merchant_id}"
"""``GET /v2/merchants/{merchant_id}`` -- ``operation_id="RetrieveMerchant"``."""
RETRIEVE_ORDER = "/v2/orders/{order_id}"
"""``GET /v2/orders/{order_id}`` -- ``operation_id="RetrieveOrder"``."""
RETRIEVE_TOKEN_STATUS = "/oauth2/token/status"
"""``POST /oauth2/token/status`` -- ``operation_id="RetrieveTokenStatus"``."""
RETRIEVE_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions/{subscription_id}"
"""``GET /v2/webhooks/subscriptions/{subscription_id}`` -- ``operation_id="RetrieveWebhookSubscription"``."""
REVOKE_TOKEN = "/oauth2/revoke"
"""``POST /oauth2/revoke`` -- ``operation_id="RevokeToken"``."""
SEARCH_CATALOG_OBJECTS = "/v2/catalog/search"
"""``POST /v2/catalog/search`` -- ``operation_id="SearchCatalogObjects"``."""
SEARCH_LOYALTY_ACCOUNTS = "/v2/loyalty/accounts/search"
"""``POST /v2/loyalty/accounts/search`` -- ``operation_id="SearchLoyaltyAccounts"``."""
SEARCH_ORDERS = "/v2/orders/search"
"""``POST /v2/orders/search`` -- ``operation_id="SearchOrders"``."""
TEST_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions/{subscription_id}/test"
"""``POST /v2/webhooks/subscriptions/{subscription_id}/test`` -- ``operation_id="TestWebhookSubscription"``."""
UPDATE_ORDER = "/v2/orders/{order_id}"
"""``PUT /v2/orders/{order_id}`` -- ``operation_id="UpdateOrder"``."""
UPSERT_CATALOG_OBJECT = "/v2/catalog/object"
"""``POST /v2/catalog/object`` -- ``operation_id="UpsertCatalogObject"``."""
