"""Every identifier the shipped seed scenario contains, as importable names.

FOR: giving unit tests, integration tests, the conformance fidelity cases and a
consumer's own fixtures one place to learn what is in the default scenario, so
that ``"CAISENgvlJ6jLWAzERDzjyHVybY"`` is never typed twice and a change to the
document is a change to one file.

INVARIANT: **these constants and ``default.seed.json`` agree.** A test asserts
every name below against the document as shipped, so editing one without the
other is a red run rather than a fixture that silently stops matching anything.

The values are taken from Square's own documentation examples, so a consumer
reading the docs recognises what comes back:

===================  =======================================================================
merchant, location   https://developer.squareup.com/reference/square/locations-api/list-locations
catalog item         https://developer.squareup.com/reference/square/catalog-api/retrieve-catalog-object
order id shape       https://developer.squareup.com/reference/square/orders-api/create-order
===================  =======================================================================
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
    "SEED_KIOSK_LOCATION_ID",
    "SEED_LOCATION_ID",
    "SEED_LOYALTY_ACCOUNT_ID",
    "SEED_LOYALTY_ACCOUNT_PHONE",
    "SEED_LOYALTY_CUSTOMER_ID",
    "SEED_LOYALTY_PROGRAM_ID",
    "SEED_LOYALTY_REWARD_TIER_ID",
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
"""When the COMPLETED order reached its terminal state.

"The timestamp for when the order reached a terminal state, in RFC 3339
format" (https://developer.squareup.com/reference/square/objects/Order). A
terminal order in the scenario has to have one, or a ``closed_at`` date filter
-- the documented pairing with ``sort_field: CLOSED_AT`` -- matches nothing
over the only terminal order the unit ships, and a consumer debugging that
query concludes the query is wrong.
"""

SEED_COMPLETED_ORDER_TENDER_ID = "EnZdNAlWCmfh6Mt5FMNST1o7taB"
"""The tender that paid the COMPLETED order, id and shape from Square's
PayOrder example response
(https://developer.squareup.com/reference/square/orders-api/pay-order). It
covers the whole 1125-minor-unit total, because "Completed orders are fully
paid" (https://developer.squareup.com/reference/square/enums/OrderState)."""

SEED_COMPLETED_ORDER_TOTAL = 1125
"""3 x the 375 Cold Brew Small, in minor units: the order total, and therefore
the tender amount and the reason nothing is due on it."""

SEED_LOYALTY_PROGRAM_ID = "d619f755-2d17-41f3-990d-c04ecedd64dd"
"""The program id from Square's RetrieveLoyaltyProgram example
(https://developer.squareup.com/reference/square/loyalty-api/retrieve-loyalty-program)."""

SEED_LOYALTY_REWARD_TIER_ID = "e1b39225-9da5-43d1-a5db-782cdd8ad94f"
"""The reward tier id from the same example."""

SEED_LOYALTY_SPEND_AMOUNT = 100
"""The seeded SPEND accrual rule: one point per 100 minor units. JUDGMENT --
the numbers are this scenario's, chosen so that a 500-cent order earns a
round five points; Square's example program is one point per dollar too."""

SEED_LOYALTY_ACCOUNT_ID = "79b807d2-d786-46a9-933b-918028d7a8c5"
SEED_LOYALTY_ACCOUNT_PHONE = "+14155551234"
SEED_LOYALTY_CUSTOMER_ID = "QPTXM8PQNX3Q726ZYHPMNP46XC"
"""The seeded buyer: ids from Square's loyalty examples, phone in the E.164
form the mapping documents."""

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
    "DEVELOPER_APPLICATION_WEBHOOKS_WRITE",
)
"""The full seeded grant.

The last entry is this unit's stand-in for the application credential Square
requires on the Webhook Subscriptions API -- see
:data:`~vendorfake.square.config.WEBHOOK_SUBSCRIPTIONS_SCOPE` for why an
application-owned API is modelled as a scope here. It is deliberately absent
from :data:`SEED_READ_ONLY_SCOPES`, so "a read-only token cannot register a
subscriber or read a signing key" is testable against a token the fixtures
already define.
"""

SEED_READ_ONLY_ACCESS_TOKEN = "EAAAl-unit-seeded-access-token-read-only"
SEED_READ_ONLY_REFRESH_TOKEN = "EQAAl-unit-seeded-refresh-token-read-only"
SEED_READ_ONLY_SCOPES: tuple[str, ...] = (
    "MERCHANT_PROFILE_READ",
    "ORDERS_READ",
    "ITEMS_READ",
    "PAYMENTS_READ",
    "LOYALTY_READ",
)
"""A second token that cannot write, so "403 on the write path" is testable
without minting anything."""
