"""The seed scenario: what a clover unit's world looks like at start.

One merchant ("Harvest & Rye"), two tax rates, three items (the documented
"Craft Beer" at 750 among them), a modifier group, two employees, two tenders,
two order types, the default service charge, a customer, one open order with
a client-set total, and two pre-minted bearers -- full-permission and
read-only -- and one pre-verified webhook subscriber. :mod:`.constants`
names every id; :mod:`.document` is the schema; :mod:`.hydrate` loads it.
"""

from __future__ import annotations

from vendorfake.clover.seed.constants import (
    CUSTOMER_ADA_ID,
    DEFAULT_SEED_PATH,
    EMPLOYEE_BARISTA_ID,
    EMPLOYEE_OWNER_ID,
    ITEM_BEER_ID,
    ITEM_CROISSANT_ID,
    ITEM_ESPRESSO_ID,
    MODIFIER_GROUP_MILK_ID,
    MODIFIER_OAT_ID,
    MODIFIER_SOY_ID,
    ORDER_TYPE_DINE_IN_ID,
    ORDER_TYPE_TAKE_OUT_ID,
    SEED_ACCESS_TOKEN,
    SEED_MERCHANT_ID,
    SEED_OPEN_ORDER_ID,
    SEED_OPEN_ORDER_LINE_ID,
    SEED_OPEN_ORDER_TOTAL,
    SEED_PERMISSIONS,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_READ_ONLY_PERMISSIONS,
    SEED_READ_ONLY_REFRESH_TOKEN,
    SEED_REFRESH_TOKEN,
    SEED_WEBHOOK_AUTH_CODE,
    SEED_WEBHOOK_SUBSCRIPTION_ID,
    SEED_WEBHOOK_URL,
    SERVICE_CHARGE_DEFAULT_ID,
    TAX_BEVERAGE_ID,
    TAX_BEVERAGE_RATE,
    TAX_DEFAULT_ID,
    TAX_DEFAULT_RATE,
    TENDER_CASH_ID,
    TENDER_EXTERNAL_ID,
)
from vendorfake.clover.seed.document import SeedDocument, parse_seed_document
from vendorfake.clover.seed.hydrate import SEED_META, hydrate_clover

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
    "SEED_META",
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
    "SeedDocument",
    "hydrate_clover",
    "parse_seed_document",
]
