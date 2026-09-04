"""Hand-written path constants for Lightspeed, one per route with an operation_id.

FOR: a consumer who wants ``paths.LIST_OUTLETS`` instead of a route path typed
into every test -- and a route path that CANNOT drift from what the router
actually serves, because ``tests/unit/test_paths_drift.py`` builds a real
Lightspeed vendor, reads its route table and asserts these constants against
it: one constant per non-internal route carrying an ``operation_id``, values
equal, and no constant naming a route that does not exist.

Constants are ``UPPER_SNAKE`` of the route's ``operation_id`` -- the same
identifier :func:`vendorfake.registry.routes` and ``GET /__unit/routes``
publish. **Only** such constants live here: the drift test reads every
``UPPER_SNAKE`` string this module defines and fails on one that names no
route, so the two base-path prefixes below are private and re-exported from
``surface/common.py`` for anything that needs them.

THE TWO BASE PATHS, both DOCUMENTED. The resource API is served under
``/api/2026-07`` (the document's own ``servers`` entry,
``https://{domain_prefix}.retail.lightspeed.app/api/2026-07``); the token
endpoint is under ``/api/1.0`` on the same host
(https://x-series-api.lightspeedhq.com/docs/authorization). The authorize
redirect is on a *different host* in the real API
(``secure.retail.lightspeed.app/connect``); this unit serves one origin, so
:data:`CONNECT` is a stand-in at the documented path and nothing else -- see
``surface/auth.py``.

JUDGMENT -- **five operation ids are this project's.** The Webhooks tag's own
``operationId``s in the specification are ``get-webhooks``, ``post-webhooks``,
``get-webhooks-id``, ``put-webhooks-id`` and ``delete-webhooks-webhookId``:
hyphenated, so no Python constant can be named after them and the
``UPPER_SNAKE`` convention every other vendor's ``paths.py`` follows cannot
apply. They are spelled ``ListWebhooks``/``CreateWebhook``/``GetWebhook``/
``UpdateWebhook``/``DeleteWebhook`` here, in the CamelCase style the rest of
the document uses, and each route's summary quotes the vendor's own id. The
fidelity extract matches on ``METHOD /path`` and never on an operation id, so
nothing downstream depends on the vendor's spelling.

Do not hand-edit a value without also fixing the route it names, or the drift
test fails naming exactly which constant disagrees with the router.
"""

from __future__ import annotations

__all__ = [
    "CLOSE_REGISTER",
    "CONNECT",
    "CREATE_CUSTOMER",
    "CREATE_PRODUCT",
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
    "GET_WEBHOOK",
    "LIST_CUSTOMERS",
    "LIST_INVENTORY_LEVELS",
    "LIST_INVENTORY_RECORDS",
    "LIST_OUTLETS",
    "LIST_PAYMENT_TYPES",
    "LIST_PRODUCTS",
    "LIST_PRODUCT_INVENTORY_LEVELS",
    "LIST_PRODUCT_INVENTORY_RECORDS",
    "LIST_REGISTERS",
    "LIST_STOCK_ADJUSTMENTS",
    "LIST_WEBHOOKS",
    "OPEN_REGISTER",
    "REGISTER_PAYMENTS_SUMMARY",
    "TOKEN_EXCHANGE",
    "UPDATE_CUSTOMER_BY_ID",
    "UPDATE_PRODUCT",
    "UPDATE_WEBHOOK",
]

_API_PREFIX = "/api/2026-07"
_TOKEN_PREFIX = "/api/1.0"


def _api(suffix: str) -> str:
    return f"{_API_PREFIX}{suffix}"


CLOSE_REGISTER = _api("/registers/{register_id}/actions/close")
"""``PUT /api/2026-07/registers/{register_id}/actions/close`` -- ``operation_id="CloseRegister"``."""
CONNECT = "/connect"
"""``GET /connect`` -- ``operation_id="Connect"``. A stand-in; see ``surface/auth.py``."""
CREATE_CUSTOMER = _api("/customers")
"""``POST /api/2026-07/customers`` -- ``operation_id="CreateCustomer"``."""
CREATE_PRODUCT = _api("/products")
"""``POST /api/2026-07/products`` -- ``operation_id="CreateProduct"``."""
CREATE_STOCK_ADJUSTMENTS = _api("/stock_adjustments")
"""``POST /api/2026-07/stock_adjustments`` -- ``operation_id="CreateStockAdjustments"``."""
CREATE_WEBHOOK = _api("/webhooks")
"""``POST /api/2026-07/webhooks`` -- ``operation_id="CreateWebhook"``."""
DELETE_CUSTOMER_BY_ID = _api("/customers/{customer_id}")
"""``DELETE /api/2026-07/customers/{customer_id}`` -- ``operation_id="DeleteCustomerByID"``."""
DELETE_PRODUCT = _api("/products/{product_id}")
"""``DELETE /api/2026-07/products/{product_id}`` -- ``operation_id="DeleteProduct"``."""
DELETE_WEBHOOK = _api("/webhooks/{webhookId}")
"""``DELETE /api/2026-07/webhooks/{webhookId}`` -- ``operation_id="DeleteWebhook"``."""
GET_CUSTOMER_BY_ID = _api("/customers/{customer_id}")
"""``GET /api/2026-07/customers/{customer_id}`` -- ``operation_id="GetCustomerByID"``."""
GET_OUTLET_BY_ID = _api("/outlets/{outlet_id}")
"""``GET /api/2026-07/outlets/{outlet_id}`` -- ``operation_id="GetOutletByID"``."""
GET_PRODUCT_BY_ID = _api("/products/{product_id}")
"""``GET /api/2026-07/products/{product_id}`` -- ``operation_id="GetProductByID"``."""
GET_REGISTER_BY_ID = _api("/registers/{register_id}")
"""``GET /api/2026-07/registers/{register_id}`` -- ``operation_id="GetRegisterByID"``."""
GET_RETAILER = _api("/retailer")
"""``GET /api/2026-07/retailer`` -- ``operation_id="GetRetailer"``."""
GET_WEBHOOK = _api("/webhooks/{webhookId}")
"""``GET /api/2026-07/webhooks/{webhookId}`` -- ``operation_id="GetWebhook"``."""
LIST_CUSTOMERS = _api("/customers")
"""``GET /api/2026-07/customers`` -- ``operation_id="ListCustomers"``."""
LIST_INVENTORY_LEVELS = _api("/inventory_levels")
"""``POST /api/2026-07/inventory_levels`` -- ``operation_id="ListInventoryLevels"``. A POST that reads: the
query travels in the body as ``InventoryLevelsRequest``."""
LIST_INVENTORY_RECORDS = _api("/inventory")
"""``POST /api/2026-07/inventory`` -- ``operation_id="ListInventoryRecords"``. A POST that reads, likewise."""
LIST_OUTLETS = _api("/outlets")
"""``GET /api/2026-07/outlets`` -- ``operation_id="ListOutlets"``."""
LIST_PAYMENT_TYPES = _api("/payment_types")
"""``GET /api/2026-07/payment_types`` -- ``operation_id="ListPaymentTypes"``."""
LIST_PRODUCTS = _api("/products")
"""``GET /api/2026-07/products`` -- ``operation_id="ListProducts"``."""
LIST_PRODUCT_INVENTORY_LEVELS = _api("/inventory_levels/{product_id}")
"""``GET /api/2026-07/inventory_levels/{product_id}`` -- ``operation_id="ListProductInventoryLevels"``."""
LIST_PRODUCT_INVENTORY_RECORDS = _api("/inventory/{product_id}")
"""``GET /api/2026-07/inventory/{product_id}`` -- ``operation_id="ListProductInventoryRecords"``."""
LIST_REGISTERS = _api("/registers")
"""``GET /api/2026-07/registers`` -- ``operation_id="ListRegisters"``."""
LIST_STOCK_ADJUSTMENTS = _api("/stock_adjustments")
"""``GET /api/2026-07/stock_adjustments`` -- ``operation_id="ListStockAdjustments"``."""
LIST_WEBHOOKS = _api("/webhooks")
"""``GET /api/2026-07/webhooks`` -- ``operation_id="ListWebhooks"``."""
OPEN_REGISTER = _api("/registers/{register_id}/actions/open")
"""``PUT /api/2026-07/registers/{register_id}/actions/open`` -- ``operation_id="OpenRegister"``."""
REGISTER_PAYMENTS_SUMMARY = _api("/registers/{register_id}/payments_summary")
"""``GET /api/2026-07/registers/{register_id}/payments_summary`` -- ``operation_id="RegisterPaymentsSummary"``."""
TOKEN_EXCHANGE = f"{_TOKEN_PREFIX}/token"
"""``POST /api/1.0/token`` -- ``operation_id="TokenExchange"``."""
UPDATE_CUSTOMER_BY_ID = _api("/customers/{customer_id}")
"""``PUT /api/2026-07/customers/{customer_id}`` -- ``operation_id="UpdateCustomerByID"``."""
UPDATE_PRODUCT = _api("/products/{product_id}")
"""``PUT /api/2026-07/products/{product_id}`` -- ``operation_id="UpdateProduct"``."""
UPDATE_WEBHOOK = _api("/webhooks/{webhookId}")
"""``PUT /api/2026-07/webhooks/{webhookId}`` -- ``operation_id="UpdateWebhook"``."""
