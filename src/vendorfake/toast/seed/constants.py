"""Every identifier the shipped seed scenario contains, as importable names.

FOR: giving unit tests, the conformance target and a consumer's own fixtures
one place to learn what is in the default scenario.

INVARIANT: **these constants and ``default.seed.json`` agree.** A test asserts
every name below against the document as shipped.

Guids are lowercase UUIDs, the one documented shape (``ids.py``); the ones
below are readable-on-purpose fakes (a ``...-4000-8000-...`` middle so they
also pass a v4 shape check). ``multiLocationId`` values are 18-digit numeric
strings like the documented ``100000000171239701``. The seeded tokens are
readable strings rather than JWTs because a human types them into curl
commands (JUDGMENT, the same call the Clover seed makes); every token the
login route mints is JWT-shaped.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "ALT_PAYMENT_EXTERNAL_GUID",
    "CREDIT_AUTHORIZATION_CENTS",
    "CREDIT_AUTHORIZATION_GUID",
    "DEFAULT_SEED_PATH",
    "DINING_OPTION_DINE_IN_GUID",
    "DINING_OPTION_TAKE_OUT_GUID",
    "DISCOUNT_REGULARS_GUID",
    "DISCOUNT_SOUP_GUID",
    "GROUP_DRINKS_GUID",
    "GROUP_MAINS_GUID",
    "ITEM_BURGER_GUID",
    "ITEM_LEMONADE_GUID",
    "ITEM_SOUP_GUID",
    "ITEM_SOUP_MULTI_LOCATION_ID",
    "ITEM_SOUP_PRICE_CENTS",
    "MENU_GUID",
    "MENU_LAST_UPDATED_MS",
    "MODIFIER_GROUP_SIDES_GUID",
    "MODIFIER_GROUP_SIDES_REF",
    "MODIFIER_OPTION_FRIES_GUID",
    "MODIFIER_OPTION_FRIES_REF",
    "MODIFIER_OPTION_SALAD_GUID",
    "MODIFIER_OPTION_SALAD_REF",
    "PRE_MODIFIER_EXTRA_GUID",
    "PRE_MODIFIER_GROUP_REF",
    "PRE_MODIFIER_NO_GUID",
    "RESTAURANT_SERVICE_DINNER_GUID",
    "REVENUE_CENTER_GUID",
    "SEED_ACCESS_TOKEN",
    "SEED_CLIENT_ID",
    "SEED_CLIENT_SECRET",
    "SEED_CONFIG_MODIFIED_MS",
    "SEED_MANAGEMENT_GROUP_GUID",
    "SEED_ORDER_BUSINESS_DATE",
    "SEED_ORDER_CHECK_GUID",
    "SEED_ORDER_GUID",
    "SEED_ORDER_OPENED_MS",
    "SEED_ORDER_SELECTION_GUID",
    "SEED_PARTNER_GUID",
    "SEED_READ_ONLY_ACCESS_TOKEN",
    "SEED_READ_ONLY_SCOPES",
    "SEED_RESTAURANT_GUID",
    "SEED_RESTAURANT_NAME",
    "SEED_SCOPES",
    "SERVICE_AREA_GUID",
    "SERVICE_CHARGE_GRATUITY_GUID",
    "TABLE_1_GUID",
    "TABLE_2_GUID",
    "TAX_RATE_DEFAULT_GUID",
    "TAX_RATE_DEFAULT_RATE",
    "VOID_REASON_GUID",
]

DEFAULT_SEED_PATH = Path(__file__).resolve().parent / "default.seed.json"

SEED_RESTAURANT_GUID = "e6a4a8d2-0000-4000-8000-000000000001"
SEED_RESTAURANT_NAME = "Harvest & Rye — Toast"
SEED_MANAGEMENT_GROUP_GUID = "e6a4a8d2-0000-4000-8000-0000000000a1"
"""The restaurant's ``managementGroupGuid`` -- also what the auth adapter
refuses in ``Toast-Restaurant-External-ID`` ("cannot be the GUID of a
restaurant group")."""

SEED_CLIENT_ID = "unit-toast-client-id"
SEED_CLIENT_SECRET = "unit-toast-client-secret"
SEED_PARTNER_GUID = "0f6c1b1e-0000-4000-8000-00000000a0a0"

SEED_ACCESS_TOKEN = "unit-seeded-toast-access-token-full-scopes"
SEED_SCOPES: tuple[str, ...] = (
    "orders:read",
    "orders.channel:read",
    "orders.payments:write",
    "guest.pi:read",
    "delivery_info.address:read",
    "orders:write",
    "menus:read",
    "config:read",
    "restaurants:read",
    "partners:read",
    "stock:read",
    "stock:write",
)

SEED_READ_ONLY_ACCESS_TOKEN = "unit-seeded-toast-access-token-read-only"
SEED_READ_ONLY_SCOPES: tuple[str, ...] = (
    "orders:read",
    "menus:read",
    "config:read",
    "restaurants:read",
    "partners:read",
    "stock:read",
)

SEED_CONFIG_MODIFIED_MS = 1755786102000
"""The ``modified_ms`` every seeded config entity carries (2025-08-21T14:21:42Z),
so a ``lastModified`` filter has a fixed instant to compare against."""

MENU_GUID = "3c9a1f00-0000-4000-8000-00000000c001"
MENU_LAST_UPDATED_MS = 1755786102000
GROUP_MAINS_GUID = "3c9a1f00-0000-4000-8000-00000000c101"
GROUP_DRINKS_GUID = "3c9a1f00-0000-4000-8000-00000000c102"

ITEM_SOUP_GUID = "3c9a1f00-0000-4000-8000-00000000c201"
ITEM_SOUP_MULTI_LOCATION_ID = "100000000171238879"
ITEM_SOUP_PRICE_CENTS = 899
"""8.99 -- the documented ``/prices`` example: at the default 6.25% rate the
tax is 0.56 and the total 9.55 (apiOrderPrices.html)."""
ITEM_BURGER_GUID = "3c9a1f00-0000-4000-8000-00000000c202"
ITEM_LEMONADE_GUID = "3c9a1f00-0000-4000-8000-00000000c203"

MODIFIER_GROUP_SIDES_REF = 2
MODIFIER_GROUP_SIDES_GUID = "3c9a1f00-0000-4000-8000-00000000c301"
MODIFIER_OPTION_FRIES_REF = 6
MODIFIER_OPTION_FRIES_GUID = "3c9a1f00-0000-4000-8000-00000000c401"
MODIFIER_OPTION_SALAD_REF = 7
MODIFIER_OPTION_SALAD_GUID = "3c9a1f00-0000-4000-8000-00000000c402"
PRE_MODIFIER_GROUP_REF = 10
PRE_MODIFIER_NO_GUID = "3c9a1f00-0000-4000-8000-00000000c501"
PRE_MODIFIER_EXTRA_GUID = "3c9a1f00-0000-4000-8000-00000000c502"
"""The V3 maps are keyed by small integer ``referenceId``s; 2, 6 and 10 are
the ones the documentation example shows."""

DINING_OPTION_DINE_IN_GUID = "5d0e2b11-0000-4000-8000-00000000d001"
DINING_OPTION_TAKE_OUT_GUID = "5d0e2b11-0000-4000-8000-00000000d002"
ALT_PAYMENT_EXTERNAL_GUID = "5d0e2b11-0000-4000-8000-00000000d101"
"""The alternate payment type an OTHER payment names in ``otherPayment.guid``."""
TAX_RATE_DEFAULT_GUID = "5d0e2b11-0000-4000-8000-00000000d201"
TAX_RATE_DEFAULT_RATE = 0.0625
REVENUE_CENTER_GUID = "5d0e2b11-0000-4000-8000-00000000d301"
SERVICE_AREA_GUID = "5d0e2b11-0000-4000-8000-00000000d401"
TABLE_1_GUID = "5d0e2b11-0000-4000-8000-00000000d501"
TABLE_2_GUID = "5d0e2b11-0000-4000-8000-00000000d502"
RESTAURANT_SERVICE_DINNER_GUID = "5d0e2b11-0000-4000-8000-00000000d601"
DISCOUNT_SOUP_GUID = "5d0e2b11-0000-4000-8000-00000000d701"
"""'Enjoy more soup.' -- the documented AppliedDiscount example's name; a 100%
ITEM discount, so applying it to the 8.99 soup reproduces the example's
``discountAmount`` 8.99."""
SERVICE_CHARGE_GRATUITY_GUID = "5d0e2b11-0000-4000-8000-00000000d801"
VOID_REASON_GUID = "5d0e2b11-0000-4000-8000-00000000d901"

DISCOUNT_REGULARS_GUID = "5d0e2b11-0000-4000-8000-00000000d702"
"""A 10% CHECK-type discount, so the check-level route has something to apply."""

SEED_ORDER_GUID = "9a7b6c5d-0000-4000-8000-00000000f001"
SEED_ORDER_CHECK_GUID = "9a7b6c5d-0000-4000-8000-00000000f101"
SEED_ORDER_SELECTION_GUID = "9a7b6c5d-0000-4000-8000-00000000f201"
SEED_ORDER_OPENED_MS = 1755786102000
SEED_ORDER_BUSINESS_DATE = 20250821
"""The existing order: one Lemonade (3.25, tax 0.20, total 3.45), unpaid,
opened 2025-08-21T14:21:42Z which is 10:21 in New York -- business date
20250821 after the 4 am closeout."""

CREDIT_AUTHORIZATION_GUID = "7c65cc16-0000-4000-8000-00000000e001"
CREDIT_AUTHORIZATION_CENTS = 5000
"""The one pre-authorised card payment; a CREDIT payment names it in ``guid``."""
