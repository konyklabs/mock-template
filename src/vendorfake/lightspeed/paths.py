"""Hand-written path constants for Lightspeed, one per route with an operation_id,
as ``UPPER_SNAKE``; ``tests/unit/test_paths_drift.py`` checks each against the
router's own table.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/authorization): the resource
API is served under ``/api/2026-07`` and the token endpoint under ``/api/1.0``, both
on one host; the real authorize redirect is on a different host, so :data:`CONNECT`
is a stand-in (see ``surface/auth.py``).

JUDGMENT: the Webhooks tag's five hyphenated operation ids keep the document's own
CamelCase spelling instead of ``UPPER_SNAKE``; the fidelity extract matches on
``METHOD /path``, never an id.
"""

from __future__ import annotations

__all__ = [
    "CLOSE_REGISTER",
    "CONNECT",
    "CREATE_CUSTOMER",
    "CREATE_PRODUCT",
    "CREATE_SALE",
    "CREATE_STOCK_ADJUSTMENTS",
    "CREATE_WEBHOOK",
    "DELETE_CUSTOMER_BY_ID",
    "DELETE_PRODUCT",
    "DELETE_WEBHOOK",
    "GET_CUSTOMER_BY_ID",
    "GET_OUTLET_BY_ID",
    "GET_PRODUCT_BY_ID",
    "GET_REGISTER_BY_ID",
    "GET_RETAILER",
    "GET_SALE_BY_ID",
    "GET_WEBHOOK",
    "INIT_RETURN_SALE",
    "LIST_CUSTOMERS",
    "LIST_INVENTORY_LEVELS",
    "LIST_INVENTORY_RECORDS",
    "LIST_OUTLETS",
    "LIST_PAYMENT_TYPES",
    "LIST_PRODUCTS",
    "LIST_PRODUCT_INVENTORY_LEVELS",
    "LIST_PRODUCT_INVENTORY_RECORDS",
    "LIST_REGISTERS",
    "LIST_SALES",
    "LIST_STOCK_ADJUSTMENTS",
    "LIST_WEBHOOKS",
    "OPEN_REGISTER",
    "REGISTER_PAYMENTS_SUMMARY",
    "TOKEN_EXCHANGE",
    "UPDATE_CUSTOMER_BY_ID",
    "UPDATE_PRODUCT",
    "UPDATE_SALE",
    "UPDATE_WEBHOOK",
]

_API_PREFIX = "/api/2026-07"
_TOKEN_PREFIX = "/api/1.0"


def _api(suffix: str) -> str:
    return f"{_API_PREFIX}{suffix}"


CLOSE_REGISTER = _api("/registers/{register_id}/actions/close")
CONNECT = "/connect"
CREATE_CUSTOMER = _api("/customers")
CREATE_PRODUCT = _api("/products")
CREATE_SALE = _api("/sales")
CREATE_STOCK_ADJUSTMENTS = _api("/stock_adjustments")
CREATE_WEBHOOK = _api("/webhooks")
DELETE_CUSTOMER_BY_ID = _api("/customers/{customer_id}")
DELETE_PRODUCT = _api("/products/{product_id}")
DELETE_WEBHOOK = _api("/webhooks/{webhookId}")
GET_CUSTOMER_BY_ID = _api("/customers/{customer_id}")
GET_OUTLET_BY_ID = _api("/outlets/{outlet_id}")
GET_PRODUCT_BY_ID = _api("/products/{product_id}")
GET_REGISTER_BY_ID = _api("/registers/{register_id}")
GET_RETAILER = _api("/retailer")
GET_SALE_BY_ID = _api("/sales/{sale_id}")
GET_WEBHOOK = _api("/webhooks/{webhookId}")
INIT_RETURN_SALE = _api("/sales/{sale_id}/actions/return")
"""Kept as the vendor's own camelCase id (``initReturnSale``), unlike the hyphenated webhook ids."""
LIST_CUSTOMERS = _api("/customers")
LIST_INVENTORY_LEVELS = _api("/inventory_levels")
"""``POST`` despite the name -- the query travels in the body as ``InventoryLevelsRequest``."""
LIST_INVENTORY_RECORDS = _api("/inventory")
"""``POST`` despite the name, likewise."""
LIST_OUTLETS = _api("/outlets")
LIST_PAYMENT_TYPES = _api("/payment_types")
LIST_PRODUCTS = _api("/products")
LIST_PRODUCT_INVENTORY_LEVELS = _api("/inventory_levels/{product_id}")
LIST_PRODUCT_INVENTORY_RECORDS = _api("/inventory/{product_id}")
LIST_REGISTERS = _api("/registers")
LIST_SALES = _api("/sales")
LIST_STOCK_ADJUSTMENTS = _api("/stock_adjustments")
LIST_WEBHOOKS = _api("/webhooks")
OPEN_REGISTER = _api("/registers/{register_id}/actions/open")
REGISTER_PAYMENTS_SUMMARY = _api("/registers/{register_id}/payments_summary")
TOKEN_EXCHANGE = f"{_TOKEN_PREFIX}/token"
"""Under ``/api/1.0``, not ``/api/2026-07`` like every other constant here."""
UPDATE_CUSTOMER_BY_ID = _api("/customers/{customer_id}")
UPDATE_PRODUCT = _api("/products/{product_id}")
UPDATE_SALE = _api("/sales/{sale_id}")
UPDATE_WEBHOOK = _api("/webhooks/{webhookId}")
