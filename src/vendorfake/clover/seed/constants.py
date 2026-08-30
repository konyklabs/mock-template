"""Every identifier the shipped seed scenario contains, as importable names.

FOR: giving unit tests, the conformance target and a consumer's own fixtures
one place to learn what is in the default scenario, so that ``HRVSTRYE12345``
is never typed twice and a change to the document is a change to one file.

INVARIANT: **these constants and ``default.seed.json`` agree.** A test asserts
every name below against the document as shipped, so editing one without the
other is a red run rather than a fixture that silently stops matching anything.

Ids are 13 uppercase alphanumerics, the shape every Clover example uses
(``ids.py``); the readable ones below are obviously fake on purpose, exactly
like the Square seed's. ``KFRPRVCZ73JHM`` is the order type id in Clover's own
create-order example (https://docs.clover.com/dev/docs/creating-custom-orders),
and "Craft Beer" at 750 is the documented create-item example
(https://docs.clover.com/dev/reference/inventorycreateitem).
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CUSTOMER_ADA_ID",
    "DEFAULT_SEED_PATH",
    "EMPLOYEE_BARISTA_ID",
    "EMPLOYEE_OWNER_ID",
    "ITEM_BEER_ID",
    "ITEM_CROISSANT_ID",
    "ITEM_ESPRESSO_ID",
    "MODIFIER_GROUP_MILK_ID",
    "MODIFIER_OAT_ID",
    "MODIFIER_SOY_ID",
    "ORDER_TYPE_DINE_IN_ID",
    "ORDER_TYPE_TAKE_OUT_ID",
    "SEED_ACCESS_TOKEN",
    "SEED_MERCHANT_ID",
    "SEED_OPEN_ORDER_ID",
    "SEED_OPEN_ORDER_LINE_ID",
    "SEED_OPEN_ORDER_TOTAL",
    "SEED_PERMISSIONS",
    "SEED_READ_ONLY_ACCESS_TOKEN",
    "SEED_READ_ONLY_PERMISSIONS",
    "SEED_READ_ONLY_REFRESH_TOKEN",
    "SEED_REFRESH_TOKEN",
    "SEED_WEBHOOK_AUTH_CODE",
    "SEED_WEBHOOK_SUBSCRIPTION_ID",
    "SEED_WEBHOOK_URL",
    "SERVICE_CHARGE_DEFAULT_ID",
    "TAX_BEVERAGE_ID",
    "TAX_BEVERAGE_RATE",
    "TAX_DEFAULT_ID",
    "TAX_DEFAULT_RATE",
    "TENDER_CASH_ID",
    "TENDER_EXTERNAL_ID",
]

DEFAULT_SEED_PATH = Path(__file__).resolve().parent / "default.seed.json"
"""The shipped scenario, as a path. Profiles name it relative to the package."""

SEED_MERCHANT_ID = "HRVSTRYE12345"

EMPLOYEE_OWNER_ID = "OWNERHRVST001"
EMPLOYEE_BARISTA_ID = "EMPLBARISTA01"

TENDER_CASH_ID = "TENDERCASH001"
TENDER_EXTERNAL_ID = "TENDEREXTRN01"
"""An external-style tender (``labelKey`` ``com.clover.tender.external_payment``),
the kind ``POST .../payments`` is documented for."""

ORDER_TYPE_DINE_IN_ID = "KFRPRVCZ73JHM"
ORDER_TYPE_TAKE_OUT_ID = "ORDTYPETAKE01"

SERVICE_CHARGE_DEFAULT_ID = "SVCCHARGE0001"
"""18%: ``percentageDecimal`` 180000 at the documented percent x 10000."""

TAX_DEFAULT_ID = "TAXDEFAULT001"
TAX_DEFAULT_RATE = 725000
"""7.25%, at the JUDGMENT scale of percent x 100000 (``model/order.py``)."""
TAX_BEVERAGE_ID = "TAXBEVERAGE01"
TAX_BEVERAGE_RATE = 1000000
"""10%."""

ITEM_BEER_ID = "CRAFTBEER0750"
ITEM_ESPRESSO_ID = "ESPRESSO00300"
ITEM_CROISSANT_ID = "CROISSANT0450"

MODIFIER_GROUP_MILK_ID = "MODGROUPMILK1"
MODIFIER_OAT_ID = "MODIFIEROAT01"
MODIFIER_SOY_ID = "MODIFIERSOY01"

CUSTOMER_ADA_ID = "CUSTOMERADA01"

SEED_OPEN_ORDER_ID = "SEEDORDER0001"
SEED_OPEN_ORDER_LINE_ID = "SEEDLINE00001"
SEED_OPEN_ORDER_TOTAL = 750
"""Client-set, and equal to its one line (a Craft Beer) because the scenario
author did the arithmetic Clover leaves to the app. Nothing here recomputes
it."""

SEED_ACCESS_TOKEN = "unit-seeded-clover-access-token-full-permissions"
SEED_REFRESH_TOKEN = "unit-seeded-clover-refresh-token-full-permissions"
"""Readable and obviously fake, like the Square seed's. The stream mints
UUID-shaped tokens (``ids.py``); a seeded token is typed by humans into curl
commands and fixtures, so it is readable on purpose."""

SEED_PERMISSIONS: tuple[str, ...] = (
    "ORDERS_R",
    "ORDERS_W",
    "INVENTORY_R",
    "INVENTORY_W",
    "MERCHANT_R",
    "EMPLOYEES_R",
    "CUSTOMERS_R",
    "CUSTOMERS_W",
    "PAYMENTS_W",
)
"""The app's full permission set (``config.DEFAULT_PERMISSIONS``)."""

SEED_READ_ONLY_ACCESS_TOKEN = "unit-seeded-clover-access-token-read-only"
SEED_READ_ONLY_REFRESH_TOKEN = "unit-seeded-clover-refresh-token-read-only"
SEED_READ_ONLY_PERMISSIONS: tuple[str, ...] = ("ORDERS_R", "INVENTORY_R", "MERCHANT_R", "EMPLOYEES_R", "CUSTOMERS_R")
"""A second token that cannot write, so "401 on the write path" is testable
-- and the conformance suite's forbidden-permission clause askable -- without
minting anything."""

SEED_WEBHOOK_SUBSCRIPTION_ID = "wbhk_seed_quickstart"
SEED_WEBHOOK_URL = "https://example.test/webhooks/clover"
SEED_WEBHOOK_AUTH_CODE = "unit-seeded-clover-webhook-auth-code"
"""The pre-verified subscriber the scenario ships: every event key, delivered
with ``X-Clover-Auth: unit-seeded-clover-webhook-auth-code``. Clover's real
auth code is a UUID shown once in the dashboard; this one is readable and
obviously fake for the same reason the seeded bearers are. The callback host
is the reserved ``.test`` domain, so a served unit's deliveries to it fail and
retry on the declared schedule rather than reaching anybody -- point a
subscriber of your own at a local receiver to watch a delivery land (README)."""
