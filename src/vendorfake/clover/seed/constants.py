"""Every identifier the shipped seed scenario contains, as importable names --
a test asserts each against ``default.seed.json``, so the two must agree.

DOCUMENTED: ``KFRPRVCZ73JHM`` is Clover's create-order example id
(https://docs.clover.com/dev/docs/creating-custom-orders); "Craft Beer" at 750
is the documented create-item example
(https://docs.clover.com/dev/reference/inventorycreateitem).
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CUSTOMER_ADA_ID",
    "CUSTOMER_GRACE_ID",
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
    "SEED_SECOND_ORDER_ID",
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
"""External-style tender (``labelKey`` ``com.clover.tender.external_payment``)."""

ORDER_TYPE_DINE_IN_ID = "KFRPRVCZ73JHM"
ORDER_TYPE_TAKE_OUT_ID = "ORDTYPETAKE01"

SERVICE_CHARGE_DEFAULT_ID = "SVCCHARGE0001"
"""18%, at the documented ``percentageDecimal`` scale of percent x 10000."""

TAX_DEFAULT_ID = "TAXDEFAULT001"
TAX_DEFAULT_RATE = 725000
"""7.25%, at the JUDGMENT scale of percent x 100000 (``model/order.py``)."""
TAX_BEVERAGE_ID = "TAXBEVERAGE01"
TAX_BEVERAGE_RATE = 1000000

ITEM_BEER_ID = "CRAFTBEER0750"
ITEM_ESPRESSO_ID = "ESPRESSO00300"
ITEM_CROISSANT_ID = "CROISSANT0450"

MODIFIER_GROUP_MILK_ID = "MODGROUPMILK1"
MODIFIER_OAT_ID = "MODIFIEROAT01"
MODIFIER_SOY_ID = "MODIFIERSOY01"

CUSTOMER_ADA_ID = "CUSTOMERADA01"

SEED_SECOND_ORDER_ID = "SEEDORDER0002"
"""A second seeded order, so the orders list survives a page walk
(konyklabs/roadmap#15)."""

CUSTOMER_GRACE_ID = "GRACEHOPPER01"

SEED_OPEN_ORDER_ID = "SEEDORDER0001"
SEED_OPEN_ORDER_LINE_ID = "SEEDLINE00001"
SEED_OPEN_ORDER_TOTAL = 750
"""Client-set; Clover leaves order arithmetic to the app, so nothing recomputes it."""

SEED_ACCESS_TOKEN = "unit-seeded-clover-access-token-full-permissions"
SEED_REFRESH_TOKEN = "unit-seeded-clover-refresh-token-full-permissions"
"""Readable on purpose, unlike the stream's UUID-shaped tokens (``ids.py``)."""

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
"""A second, non-writing token, so "401 on write" is testable without minting."""

SEED_WEBHOOK_SUBSCRIPTION_ID = "wbhk_seed_quickstart"
SEED_WEBHOOK_URL = "https://example.test/webhooks/clover"
SEED_WEBHOOK_AUTH_CODE = "unit-seeded-clover-webhook-auth-code"
"""Pre-verified but disabled: its callback host is the reserved ``.test``
domain, so enabling it would fire a live retry cascade into a dead host."""
