"""Hand-written path constants for Clover, one per route with an operation_id.

FOR: a consumer who wants ``paths.REFRESH_TOKEN`` instead of a route path
typed into every test -- and a route path that CANNOT drift from what the
router actually serves, because ``tests/unit/test_paths_drift.py`` builds a
real Clover vendor, reads its route table and asserts these constants against
it: one constant per non-internal route carrying an ``operation_id``, values
equal, and no constant naming a route that does not exist.

Constants are ``UPPER_SNAKE`` of the route's ``operation_id`` -- the same
identifier :func:`vendorfake.registry.routes` and ``GET /__unit/routes``
publish. Path templates keep the brace form (``{mId}``, ``{orderId}``),
matching every other place a template is written in this project; Clover's own
``{mId}`` segment is the merchant every route below the OAuth surface is
scoped to.

Do not hand-edit a value without also fixing the route it names, or the drift
test fails naming exactly which constant disagrees with the router.
"""

from __future__ import annotations

__all__ = [
    "AUTHORIZE",
    "BULK_CREATE_LINE_ITEMS",
    "CHECKOUT_ATOMIC_ORDER",
    "CREATE_ATOMIC_ORDER",
    "CREATE_CUSTOMER",
    "CREATE_ITEM",
    "CREATE_LINE_ITEM",
    "CREATE_ORDER",
    "CREATE_PAYMENT",
    "CREATE_PRINT_EVENT",
    "DELETE_ORDER",
    "EXCHANGE_TOKEN",
    "GET_CUSTOMERS",
    "GET_DEFAULT_SERVICE_CHARGE",
    "GET_EMPLOYEES",
    "GET_ITEM",
    "GET_ITEMS",
    "GET_MERCHANT",
    "GET_MODIFIER",
    "GET_MODIFIERS",
    "GET_ORDER",
    "GET_ORDERS",
    "GET_ORDER_TYPES",
    "GET_TENDERS",
    "LIST_WEBHOOK_CALLBACKS",
    "REFRESH_TOKEN",
    "REGISTER_WEBHOOK_CALLBACK",
    "UPDATE_ITEM",
    "UPDATE_ORDER",
    "VERIFY_WEBHOOK_CALLBACK",
]

AUTHORIZE = "/oauth/v2/authorize"
"""``GET /oauth/v2/authorize`` -- ``operation_id="Authorize"``."""
BULK_CREATE_LINE_ITEMS = "/v3/merchants/{mId}/orders/{orderId}/bulk_line_items"
"""``POST /v3/merchants/{mId}/orders/{orderId}/bulk_line_items`` -- ``operation_id="BulkCreateLineItems"``."""
CHECKOUT_ATOMIC_ORDER = "/v3/merchants/{mId}/atomic_order/checkouts"
"""``POST /v3/merchants/{mId}/atomic_order/checkouts`` -- ``operation_id="CheckoutAtomicOrder"``."""
CREATE_ATOMIC_ORDER = "/v3/merchants/{mId}/atomic_order/orders"
"""``POST /v3/merchants/{mId}/atomic_order/orders`` -- ``operation_id="CreateAtomicOrder"``."""
CREATE_CUSTOMER = "/v3/merchants/{mId}/customers"
"""``POST /v3/merchants/{mId}/customers`` -- ``operation_id="CreateCustomer"``."""
CREATE_ITEM = "/v3/merchants/{mId}/items"
"""``POST /v3/merchants/{mId}/items`` -- ``operation_id="CreateItem"``."""
CREATE_LINE_ITEM = "/v3/merchants/{mId}/orders/{orderId}/line_items"
"""``POST /v3/merchants/{mId}/orders/{orderId}/line_items`` -- ``operation_id="CreateLineItem"``."""
CREATE_ORDER = "/v3/merchants/{mId}/orders"
"""``POST /v3/merchants/{mId}/orders`` -- ``operation_id="CreateOrder"``."""
CREATE_PAYMENT = "/v3/merchants/{mId}/orders/{orderId}/payments"
"""``POST /v3/merchants/{mId}/orders/{orderId}/payments`` -- ``operation_id="CreatePayment"``."""
CREATE_PRINT_EVENT = "/v3/merchants/{mId}/print_event"
"""``POST /v3/merchants/{mId}/print_event`` -- ``operation_id="CreatePrintEvent"``."""
DELETE_ORDER = "/v3/merchants/{mId}/orders/{orderId}"
"""``DELETE /v3/merchants/{mId}/orders/{orderId}`` -- ``operation_id="DeleteOrder"``."""
EXCHANGE_TOKEN = "/oauth/v2/token"
"""``POST /oauth/v2/token`` -- ``operation_id="ExchangeToken"``."""
GET_CUSTOMERS = "/v3/merchants/{mId}/customers"
"""``GET /v3/merchants/{mId}/customers`` -- ``operation_id="GetCustomers"``."""
GET_DEFAULT_SERVICE_CHARGE = "/v3/merchants/{mId}/default_service_charge"
"""``GET /v3/merchants/{mId}/default_service_charge`` -- ``operation_id="GetDefaultServiceCharge"``."""
GET_EMPLOYEES = "/v3/merchants/{mId}/employees"
"""``GET /v3/merchants/{mId}/employees`` -- ``operation_id="GetEmployees"``."""
GET_ITEM = "/v3/merchants/{mId}/items/{itemId}"
"""``GET /v3/merchants/{mId}/items/{itemId}`` -- ``operation_id="GetItem"``."""
GET_ITEMS = "/v3/merchants/{mId}/items"
"""``GET /v3/merchants/{mId}/items`` -- ``operation_id="GetItems"``."""
GET_MERCHANT = "/v3/merchants/{mId}"
"""``GET /v3/merchants/{mId}`` -- ``operation_id="GetMerchant"``."""
GET_MODIFIER = "/v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers/{modId}"
"""``GET /v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers/{modId}`` -- ``operation_id="GetModifier"``."""
GET_MODIFIERS = "/v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers"
"""``GET /v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers`` -- ``operation_id="GetModifiers"``."""
GET_ORDER = "/v3/merchants/{mId}/orders/{orderId}"
"""``GET /v3/merchants/{mId}/orders/{orderId}`` -- ``operation_id="GetOrder"``."""
GET_ORDER_TYPES = "/v3/merchants/{mId}/order_types"
"""``GET /v3/merchants/{mId}/order_types`` -- ``operation_id="GetOrderTypes"``."""
GET_ORDERS = "/v3/merchants/{mId}/orders"
"""``GET /v3/merchants/{mId}/orders`` -- ``operation_id="GetOrders"``."""
GET_TENDERS = "/v3/merchants/{mId}/tenders"
"""``GET /v3/merchants/{mId}/tenders`` -- ``operation_id="GetTenders"``."""
LIST_WEBHOOK_CALLBACKS = "/__clover/webhooks/subscriptions"
"""``GET /__clover/webhooks/subscriptions`` -- ``operation_id="ListWebhookCallbacks"``."""
REFRESH_TOKEN = "/oauth/v2/refresh"
"""``POST /oauth/v2/refresh`` -- ``operation_id="RefreshToken"``."""
REGISTER_WEBHOOK_CALLBACK = "/__clover/webhooks/subscriptions"
"""``POST /__clover/webhooks/subscriptions`` -- ``operation_id="RegisterWebhookCallback"``."""
UPDATE_ITEM = "/v3/merchants/{mId}/items/{itemId}"
"""``POST /v3/merchants/{mId}/items/{itemId}`` -- ``operation_id="UpdateItem"``."""
UPDATE_ORDER = "/v3/merchants/{mId}/orders/{orderId}"
"""``POST /v3/merchants/{mId}/orders/{orderId}`` -- ``operation_id="UpdateOrder"``."""
VERIFY_WEBHOOK_CALLBACK = "/__clover/webhooks/verify"
"""``POST /__clover/webhooks/verify`` -- ``operation_id="VerifyWebhookCallback"``."""
