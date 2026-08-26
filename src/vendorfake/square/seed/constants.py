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
    "SEED_COMPLETED_ORDER_ID",
    "SEED_KIOSK_LOCATION_ID",
    "SEED_LOCATION_ID",
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

SEED_ACCESS_TOKEN = "EAAAl-unit-seeded-access-token-full-scopes"
SEED_REFRESH_TOKEN = "EQAAl-unit-seeded-refresh-token-full-scopes"
SEED_SCOPES: tuple[str, ...] = (
    "MERCHANT_PROFILE_READ",
    "ORDERS_READ",
    "ORDERS_WRITE",
    "ITEMS_READ",
    "PAYMENTS_WRITE",
)

SEED_READ_ONLY_ACCESS_TOKEN = "EAAAl-unit-seeded-access-token-read-only"
SEED_READ_ONLY_REFRESH_TOKEN = "EQAAl-unit-seeded-refresh-token-read-only"
SEED_READ_ONLY_SCOPES: tuple[str, ...] = ("MERCHANT_PROFILE_READ", "ORDERS_READ", "ITEMS_READ")
"""A second token that cannot write, so "403 on the write path" is testable
without minting anything."""
