"""The seed scenario: what a lightspeed unit's world looks like at start.

One retailer ("Ridgeline Provisions"), two outlets, one register in each, three
payment types (one of them internal, so the scope's "excluding internal payment
types" is testable), six products in four families with ten inventory rows
across the two outlets, two custom adjustment reasons and two adjustments
already logged, one customer group and three customers, three sales (one
parked, one closed against the main register, one layby), a pre-issued OAuth
access/refresh pair, a read-only token, a personal token, and one webhook
subscription on ``register_closure.create``. :mod:`.constants` names every id;
:mod:`.document` is the schema; :mod:`.hydrate` loads it.
"""

from __future__ import annotations

from vendorfake.lightspeed.seed import constants
from vendorfake.lightspeed.seed.constants import (
    DEFAULT_SEED_PATH,
    SEED_ACCESS_TOKEN,
    SEED_ADJUSTMENT_REASON_FOUND_ID,
    SEED_ADJUSTMENT_REASON_SPOILED_ID,
    SEED_CLIENT_ID,
    SEED_CLIENT_SECRET,
    SEED_CUSTOMER_ADA_ID,
    SEED_CUSTOMER_BLAKE_ID,
    SEED_CUSTOMER_GROUP_ID,
    SEED_CUSTOMER_NOOR_ID,
    SEED_DOMAIN_PREFIX,
    SEED_OUTLET_MAIN_ID,
    SEED_OUTLET_SECOND_ID,
    SEED_PAYMENT_TYPE_CARD_ID,
    SEED_PAYMENT_TYPE_CASH_ID,
    SEED_PAYMENT_TYPE_INTERNAL_ID,
    SEED_PERSONAL_ACCESS_TOKEN,
    SEED_PRODUCT_BOTTLE_ID,
    SEED_PRODUCT_BOTTLE_SKU,
    SEED_PRODUCT_SOCKS_ID,
    SEED_PRODUCT_TEE_ID,
    SEED_PRODUCT_TEE_LARGE_ID,
    SEED_PRODUCT_TEE_SMALL_ID,
    SEED_PRODUCT_TRAIL_MIX_ID,
    SEED_PRODUCT_TRAIL_MIX_SKU,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_REFRESH_TOKEN,
    SEED_REGISTER_MAIN_ID,
    SEED_REGISTER_SECOND_ID,
    SEED_RETAILER_ID,
    SEED_RETAILER_NAME,
    SEED_SALE_CLOSED_ID,
    SEED_SALE_LAYBY_ID,
    SEED_SALE_SAVED_ID,
    SEED_STOCK_ADJUSTMENT_FIRST_ID,
    SEED_STOCK_ADJUSTMENT_SECOND_ID,
    SEED_TAX_ID,
    SEED_USER_ID,
    SEED_WEBHOOK_ID,
    SEED_WEBHOOK_TYPE,
    SEED_WEBHOOK_URL,
)
from vendorfake.lightspeed.seed.document import SeedDocument, parse_seed_document
from vendorfake.lightspeed.seed.hydrate import SEED_META, hydrate_lightspeed

__all__ = [
    "DEFAULT_SEED_PATH",
    "SEED_ACCESS_TOKEN",
    "SEED_ADJUSTMENT_REASON_FOUND_ID",
    "SEED_ADJUSTMENT_REASON_SPOILED_ID",
    "SEED_CLIENT_ID",
    "SEED_CLIENT_SECRET",
    "SEED_CUSTOMER_ADA_ID",
    "SEED_CUSTOMER_BLAKE_ID",
    "SEED_CUSTOMER_GROUP_ID",
    "SEED_CUSTOMER_NOOR_ID",
    "SEED_DOMAIN_PREFIX",
    "SEED_META",
    "SEED_OUTLET_MAIN_ID",
    "SEED_OUTLET_SECOND_ID",
    "SEED_PAYMENT_TYPE_CARD_ID",
    "SEED_PAYMENT_TYPE_CASH_ID",
    "SEED_PAYMENT_TYPE_INTERNAL_ID",
    "SEED_PERSONAL_ACCESS_TOKEN",
    "SEED_PRODUCT_BOTTLE_ID",
    "SEED_PRODUCT_BOTTLE_SKU",
    "SEED_PRODUCT_SOCKS_ID",
    "SEED_PRODUCT_TEE_ID",
    "SEED_PRODUCT_TEE_LARGE_ID",
    "SEED_PRODUCT_TEE_SMALL_ID",
    "SEED_PRODUCT_TRAIL_MIX_ID",
    "SEED_PRODUCT_TRAIL_MIX_SKU",
    "SEED_READ_ONLY_ACCESS_TOKEN",
    "SEED_REFRESH_TOKEN",
    "SEED_REGISTER_MAIN_ID",
    "SEED_REGISTER_SECOND_ID",
    "SEED_RETAILER_ID",
    "SEED_RETAILER_NAME",
    "SEED_SALE_CLOSED_ID",
    "SEED_SALE_LAYBY_ID",
    "SEED_SALE_SAVED_ID",
    "SEED_STOCK_ADJUSTMENT_FIRST_ID",
    "SEED_STOCK_ADJUSTMENT_SECOND_ID",
    "SEED_TAX_ID",
    "SEED_USER_ID",
    "SEED_WEBHOOK_ID",
    "SEED_WEBHOOK_TYPE",
    "SEED_WEBHOOK_URL",
    "SeedDocument",
    "constants",
    "hydrate_lightspeed",
    "parse_seed_document",
]
