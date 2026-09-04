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
    "CREATE_SALE",
    "CREATE_WEBHOOK",
    "DELETE_WEBHOOK",
    "GET_OUTLET_BY_ID",
    "GET_REGISTER_BY_ID",
    "GET_RETAILER",
    "GET_SALE_BY_ID",
    "GET_WEBHOOK",
    "INIT_RETURN_SALE",
    "LIST_OUTLETS",
    "LIST_PAYMENT_TYPES",
    "LIST_REGISTERS",
    "LIST_SALES",
    "LIST_WEBHOOKS",
    "OPEN_REGISTER",
    "REGISTER_PAYMENTS_SUMMARY",
    "TOKEN_EXCHANGE",
    "UPDATE_SALE",
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
CREATE_SALE = _api("/sales")
"""``POST /api/2026-07/sales`` -- ``operation_id="CreateSale"``."""
CREATE_WEBHOOK = _api("/webhooks")
"""``POST /api/2026-07/webhooks`` -- ``operation_id="CreateWebhook"``."""
DELETE_WEBHOOK = _api("/webhooks/{webhookId}")
"""``DELETE /api/2026-07/webhooks/{webhookId}`` -- ``operation_id="DeleteWebhook"``."""
GET_OUTLET_BY_ID = _api("/outlets/{outlet_id}")
"""``GET /api/2026-07/outlets/{outlet_id}`` -- ``operation_id="GetOutletByID"``."""
GET_REGISTER_BY_ID = _api("/registers/{register_id}")
"""``GET /api/2026-07/registers/{register_id}`` -- ``operation_id="GetRegisterByID"``."""
GET_RETAILER = _api("/retailer")
"""``GET /api/2026-07/retailer`` -- ``operation_id="GetRetailer"``."""
GET_SALE_BY_ID = _api("/sales/{sale_id}")
"""``GET /api/2026-07/sales/{sale_id}`` -- ``operation_id="GetSaleByID"``."""
GET_WEBHOOK = _api("/webhooks/{webhookId}")
"""``GET /api/2026-07/webhooks/{webhookId}`` -- ``operation_id="GetWebhook"``."""
INIT_RETURN_SALE = _api("/sales/{sale_id}/actions/return")
"""``POST /api/2026-07/sales/{sale_id}/actions/return`` -- ``operation_id="initReturnSale"``.

The specification's own operation id, camelCase with a lower-case first letter
where the rest of the document capitalises: ``UPPER_SNAKE`` of it is
``INIT_RETURN_SALE`` either way, so unlike the five webhook ids this one is
kept exactly as the vendor spells it."""
LIST_OUTLETS = _api("/outlets")
"""``GET /api/2026-07/outlets`` -- ``operation_id="ListOutlets"``."""
LIST_PAYMENT_TYPES = _api("/payment_types")
"""``GET /api/2026-07/payment_types`` -- ``operation_id="ListPaymentTypes"``."""
LIST_REGISTERS = _api("/registers")
"""``GET /api/2026-07/registers`` -- ``operation_id="ListRegisters"``."""
LIST_SALES = _api("/sales")
"""``GET /api/2026-07/sales`` -- ``operation_id="ListSales"``."""
LIST_WEBHOOKS = _api("/webhooks")
"""``GET /api/2026-07/webhooks`` -- ``operation_id="ListWebhooks"``."""
OPEN_REGISTER = _api("/registers/{register_id}/actions/open")
"""``PUT /api/2026-07/registers/{register_id}/actions/open`` -- ``operation_id="OpenRegister"``."""
REGISTER_PAYMENTS_SUMMARY = _api("/registers/{register_id}/payments_summary")
"""``GET /api/2026-07/registers/{register_id}/payments_summary`` -- ``operation_id="RegisterPaymentsSummary"``."""
TOKEN_EXCHANGE = f"{_TOKEN_PREFIX}/token"
"""``POST /api/1.0/token`` -- ``operation_id="TokenExchange"``."""
UPDATE_SALE = _api("/sales/{sale_id}")
"""``PUT /api/2026-07/sales/{sale_id}`` -- ``operation_id="UpdateSale"``."""
UPDATE_WEBHOOK = _api("/webhooks/{webhookId}")
"""``PUT /api/2026-07/webhooks/{webhookId}`` -- ``operation_id="UpdateWebhook"``."""
