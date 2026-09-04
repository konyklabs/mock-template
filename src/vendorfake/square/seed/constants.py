"""Every identifier the shipped seed scenario contains, as importable names.

Both this module and ``default.seed.json`` must agree -- a test asserts each
name below against the document as shipped, so the two files stay in sync.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "COLD_BREW_ITEM_ID",
    "COLD_BREW_LARGE_VARIATION_ID",
    "COLD_BREW_SMALL_VARIATION_ID",
    "DEFAULT_SEED_PATH",
    "SEED_ACCESS_TOKEN",
    "SEED_COMPLETED_ORDER_CLOSED_AT",
    "SEED_COMPLETED_ORDER_ID",
    "SEED_COMPLETED_ORDER_TENDER_ID",
    "SEED_COMPLETED_ORDER_TOTAL",
    "SEED_INVENTORY_CALCULATED_AT",
    "SEED_INVENTORY_COLD_BREW_SMALL_QUANTITY",
    "SEED_INVENTORY_TEA_MUG_QUANTITY",
    "SEED_KIOSK_LOCATION_ID",
    "SEED_LOCATION_ID",
    "SEED_LOYALTY_ACCOUNT_ID",
    "SEED_LOYALTY_ACCOUNT_PHONE",
    "SEED_LOYALTY_CUSTOMER_ID",
    "SEED_LOYALTY_PROGRAM_ID",
    "SEED_LOYALTY_REWARD_TIER_ID",
    "SEED_LOYALTY_SECOND_ACCOUNT_ID",
    "SEED_LOYALTY_SPEND_AMOUNT",
    "SEED_MERCHANT_ID",
    "SEED_OPEN_ORDER_ID",
    "SEED_READ_ONLY_ACCESS_TOKEN",
    "SEED_READ_ONLY_REFRESH_TOKEN",
    "SEED_READ_ONLY_SCOPES",
    "SEED_REFRESH_TOKEN",
    "SEED_SCOPES",
    "TEA_ITEM_ID",
    "TEA_MUG_VARIATION_ID",
    "TEA_POT_VARIATION_ID",
]

DEFAULT_SEED_PATH = Path(__file__).resolve().parent / "default.seed.json"
"""The shipped scenario, as a path. Profiles name it relative to the package."""

SEED_MERCHANT_ID = "MLQW2MYBY81PZ"
SEED_LOCATION_ID = "18YC4JDH91E1H"
SEED_KIOSK_LOCATION_ID = "057P5VYJ4A5X1"

TEA_ITEM_ID = "W62UWFY35CWMYGVWK6TWJDNI"
TEA_MUG_VARIATION_ID = "2TZFAOHWGG7PAK2QEXWYPZSP"
TEA_POT_VARIATION_ID = "QK5EVWFBUZTGXYVQJ4XLUIRZ"
COLD_BREW_ITEM_ID = "BJNQCF2FJ6S6UIDT65ABHLRX"
COLD_BREW_SMALL_VARIATION_ID = "HURXQOOAIC4IZSI2BEXQRYFY"
COLD_BREW_LARGE_VARIATION_ID = "GXAQQ4EAXWLFRTLFRZLDWDBJ"

SEED_OPEN_ORDER_ID = "CAISENgvlJ6jLWAzERDzjyHVybY"
SEED_COMPLETED_ORDER_ID = "CAISEM82RcpmcFBM0TfOyiHV3es"

SEED_COMPLETED_ORDER_CLOSED_AT = "2026-07-15T08:05:00.000Z"
"""When the COMPLETED order reached its terminal state (https://developer.squareup.com/reference/square/objects/Order)."""

SEED_COMPLETED_ORDER_TENDER_ID = "EnZdNAlWCmfh6Mt5FMNST1o7taB"
"""The tender that paid the COMPLETED order, covering its full total
(https://developer.squareup.com/reference/square/orders-api/pay-order); COMPLETED orders are documented as fully paid
(https://developer.squareup.com/reference/square/enums/OrderState)."""

SEED_COMPLETED_ORDER_TOTAL = 1125
"""3 x the 375 Cold Brew Small, in minor units."""

SEED_LOYALTY_PROGRAM_ID = "d619f755-2d17-41f3-990d-c04ecedd64dd"
"""Program id from Square's RetrieveLoyaltyProgram example (https://developer.squareup.com/reference/square/loyalty-api/retrieve-loyalty-program)."""

SEED_LOYALTY_REWARD_TIER_ID = "e1b39225-9da5-43d1-a5db-782cdd8ad94f"
"""The reward tier id from the same example."""

SEED_LOYALTY_SPEND_AMOUNT = 100
"""JUDGMENT: one point per 100 minor units, so a 500-cent order earns five."""

SEED_LOYALTY_SECOND_ACCOUNT_ID = "5f2b7c14-9a3e-4d68-8c01-7d54c2a90b31"
"""Second enrolled buyer (konyklabs/roadmap#15)."""

SEED_LOYALTY_ACCOUNT_ID = "79b807d2-d786-46a9-933b-918028d7a8c5"
SEED_LOYALTY_ACCOUNT_PHONE = "+14155551234"
SEED_LOYALTY_CUSTOMER_ID = "QPTXM8PQNX3Q726ZYHPMNP46XC"
"""Seeded buyer: ids from Square's loyalty examples; phone in E.164 form."""

SEED_INVENTORY_TEA_MUG_QUANTITY = "25"
SEED_INVENTORY_COLD_BREW_SMALL_QUANTITY = "8"
SEED_INVENTORY_CALCULATED_AT = "2026-08-01T09:00:00.000Z"
"""Two IN_STOCK counts at the Grant Park location; quantities are decimal
strings, as Square's ``InventoryCount`` sends them."""

SEED_ACCESS_TOKEN = "EAAAl-unit-seeded-access-token-full-scopes"
SEED_REFRESH_TOKEN = "EQAAl-unit-seeded-refresh-token-full-scopes"
SEED_SCOPES: tuple[str, ...] = (
    "MERCHANT_PROFILE_READ",
    "ORDERS_READ",
    "ORDERS_WRITE",
    "ITEMS_READ",
    "ITEMS_WRITE",
    "PAYMENTS_READ",
    "PAYMENTS_WRITE",
    "LOYALTY_READ",
    "LOYALTY_WRITE",
    "INVENTORY_READ",
    "INVENTORY_WRITE",
    "DEVELOPER_APPLICATION_WEBHOOKS_WRITE",
)
"""The full seeded grant, including the webhook-subscriptions scope withheld
from :data:`SEED_READ_ONLY_SCOPES`."""

SEED_READ_ONLY_ACCESS_TOKEN = "EAAAl-unit-seeded-access-token-read-only"
SEED_READ_ONLY_REFRESH_TOKEN = "EQAAl-unit-seeded-refresh-token-read-only"
SEED_READ_ONLY_SCOPES: tuple[str, ...] = (
    "MERCHANT_PROFILE_READ",
    "ORDERS_READ",
    "ITEMS_READ",
    "PAYMENTS_READ",
    "LOYALTY_READ",
    "INVENTORY_READ",
)
"""A second token that cannot write, so "403 on the write path" is testable
without minting anything."""
