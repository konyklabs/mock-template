"""The seed scenario: what a lightspeed unit's world looks like at start.

One retailer ("Ridgeline Provisions"), two outlets, one register in each, three
payment types (one of them internal, so the scope's "excluding internal payment
types" is testable), a pre-issued OAuth access/refresh pair, a read-only token,
a personal token, and one webhook subscription on
``register_closure.create``. :mod:`.constants` names every id;
:mod:`.document` is the schema; :mod:`.hydrate` loads it.
"""

from __future__ import annotations

from vendorfake.lightspeed.seed import constants
from vendorfake.lightspeed.seed.constants import (
    DEFAULT_SEED_PATH,
    SEED_ACCESS_TOKEN,
    SEED_CLIENT_ID,
    SEED_CLIENT_SECRET,
    SEED_CUSTOMER_ID,
    SEED_DOMAIN_PREFIX,
    SEED_OUTLET_MAIN_ID,
    SEED_OUTLET_SECOND_ID,
    SEED_PAYMENT_TYPE_CARD_ID,
    SEED_PAYMENT_TYPE_CASH_ID,
    SEED_PAYMENT_TYPE_INTERNAL_ID,
    SEED_PERSONAL_ACCESS_TOKEN,
    SEED_PRODUCT_BEANS_ID,
    SEED_PRODUCT_COFFEE_ID,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_REFRESH_TOKEN,
    SEED_REGISTER_MAIN_ID,
    SEED_REGISTER_SECOND_ID,
    SEED_RETAILER_ID,
    SEED_RETAILER_NAME,
    SEED_SALE_CLOSED_ID,
    SEED_SALE_LAYBY_ID,
    SEED_SALE_SAVED_ID,
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
    "SEED_CLIENT_ID",
    "SEED_CLIENT_SECRET",
    "SEED_CUSTOMER_ID",
    "SEED_DOMAIN_PREFIX",
    "SEED_META",
    "SEED_OUTLET_MAIN_ID",
    "SEED_OUTLET_SECOND_ID",
    "SEED_PAYMENT_TYPE_CARD_ID",
    "SEED_PAYMENT_TYPE_CASH_ID",
    "SEED_PAYMENT_TYPE_INTERNAL_ID",
    "SEED_PERSONAL_ACCESS_TOKEN",
    "SEED_PRODUCT_BEANS_ID",
    "SEED_PRODUCT_COFFEE_ID",
    "SEED_READ_ONLY_ACCESS_TOKEN",
    "SEED_REFRESH_TOKEN",
    "SEED_REGISTER_MAIN_ID",
    "SEED_REGISTER_SECOND_ID",
    "SEED_RETAILER_ID",
    "SEED_RETAILER_NAME",
    "SEED_SALE_CLOSED_ID",
    "SEED_SALE_LAYBY_ID",
    "SEED_SALE_SAVED_ID",
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
