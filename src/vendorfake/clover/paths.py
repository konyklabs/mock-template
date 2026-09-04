"""Hand-written path constants for Clover, one per route with an operation_id.

Constants are ``UPPER_SNAKE`` of the route's ``operation_id``, the same
identifier :func:`vendorfake.registry.routes` publishes. Path templates keep
the brace form (``{mId}``, ``{orderId}``).

INVARIANT: ``tests/unit/test_paths_drift.py`` asserts these against the real
route table -- do not hand-edit a value without also fixing the route it names.
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
BULK_CREATE_LINE_ITEMS = "/v3/merchants/{mId}/orders/{orderId}/bulk_line_items"
CHECKOUT_ATOMIC_ORDER = "/v3/merchants/{mId}/atomic_order/checkouts"
CREATE_ATOMIC_ORDER = "/v3/merchants/{mId}/atomic_order/orders"
CREATE_CUSTOMER = "/v3/merchants/{mId}/customers"
CREATE_ITEM = "/v3/merchants/{mId}/items"
CREATE_LINE_ITEM = "/v3/merchants/{mId}/orders/{orderId}/line_items"
CREATE_ORDER = "/v3/merchants/{mId}/orders"
CREATE_PAYMENT = "/v3/merchants/{mId}/orders/{orderId}/payments"
CREATE_PRINT_EVENT = "/v3/merchants/{mId}/print_event"
DELETE_ORDER = "/v3/merchants/{mId}/orders/{orderId}"
EXCHANGE_TOKEN = "/oauth/v2/token"
GET_CUSTOMERS = "/v3/merchants/{mId}/customers"
GET_DEFAULT_SERVICE_CHARGE = "/v3/merchants/{mId}/default_service_charge"
GET_EMPLOYEES = "/v3/merchants/{mId}/employees"
GET_ITEM = "/v3/merchants/{mId}/items/{itemId}"
GET_ITEMS = "/v3/merchants/{mId}/items"
GET_MERCHANT = "/v3/merchants/{mId}"
GET_MODIFIER = "/v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers/{modId}"
GET_MODIFIERS = "/v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers"
GET_ORDER = "/v3/merchants/{mId}/orders/{orderId}"
GET_ORDER_TYPES = "/v3/merchants/{mId}/order_types"
GET_ORDERS = "/v3/merchants/{mId}/orders"
GET_TENDERS = "/v3/merchants/{mId}/tenders"
LIST_WEBHOOK_CALLBACKS = "/__clover/webhooks/subscriptions"
REFRESH_TOKEN = "/oauth/v2/refresh"
REGISTER_WEBHOOK_CALLBACK = "/__clover/webhooks/subscriptions"
UPDATE_ITEM = "/v3/merchants/{mId}/items/{itemId}"
UPDATE_ORDER = "/v3/merchants/{mId}/orders/{orderId}"
VERIFY_WEBHOOK_CALLBACK = "/__clover/webhooks/verify"
