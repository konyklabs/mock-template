"""Hand-written path constants for Square, one per route with an operation_id. Names are ``UPPER_SNAKE`` of the
route's ``operation_id``, matching what :func:`vendorfake.registry.routes` publishes. ``tests/unit/test_paths_drift.py``
asserts every constant against the live route table, so a stale value fails that test rather than drifting silently.
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
AUTHORIZE = "/oauth2/authorize"
BATCH_CHANGE_INVENTORY = "/v2/inventory/changes/batch-create"
BATCH_RETRIEVE_INVENTORY_COUNTS = "/v2/inventory/counts/batch-retrieve"
BATCH_RETRIEVE_ORDERS = "/v2/orders/batch-retrieve"
CANCEL_PAYMENT = "/v2/payments/{payment_id}/cancel"
COMPLETE_PAYMENT = "/v2/payments/{payment_id}/complete"
CREATE_LOYALTY_ACCOUNT = "/v2/loyalty/accounts"
CREATE_ORDER = "/v2/orders"
CREATE_ORDER_AT_LOCATION = "/v2/locations/{location_id}/orders"
CREATE_PAYMENT = "/v2/payments"
CREATE_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions"
DELETE_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions/{subscription_id}"
GET_PAYMENT = "/v2/payments/{payment_id}"
LIST_CATALOG = "/v2/catalog/list"
LIST_LOCATIONS = "/v2/locations"
LIST_MERCHANTS = "/v2/merchants"
LIST_WEBHOOK_EVENT_TYPES = "/v2/webhooks/event-types"
LIST_WEBHOOK_SUBSCRIPTIONS = "/v2/webhooks/subscriptions"
OBTAIN_TOKEN = "/oauth2/token"
PAY_ORDER = "/v2/orders/{order_id}/pay"
RETRIEVE_CATALOG_OBJECT = "/v2/catalog/object/{object_id}"
RETRIEVE_INVENTORY_COUNT = "/v2/inventory/{catalog_object_id}"
RETRIEVE_LOYALTY_PROGRAM = "/v2/loyalty/programs/{program_id}"
RETRIEVE_MERCHANT = "/v2/merchants/{merchant_id}"
RETRIEVE_ORDER = "/v2/orders/{order_id}"
RETRIEVE_TOKEN_STATUS = "/oauth2/token/status"
RETRIEVE_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions/{subscription_id}"
REVOKE_TOKEN = "/oauth2/revoke"
SEARCH_CATALOG_OBJECTS = "/v2/catalog/search"
SEARCH_LOYALTY_ACCOUNTS = "/v2/loyalty/accounts/search"
SEARCH_ORDERS = "/v2/orders/search"
TEST_WEBHOOK_SUBSCRIPTION = "/v2/webhooks/subscriptions/{subscription_id}/test"
UPDATE_ORDER = "/v2/orders/{order_id}"
UPSERT_CATALOG_OBJECT = "/v2/catalog/object"
