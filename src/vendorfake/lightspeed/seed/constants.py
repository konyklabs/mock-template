"""Every id and credential the shipped scenario contains, importable by name,
for :class:`~vendorfake.testing.seeds.LightspeedSeed` and tests.
Invariant: these constants and ``default.seed.json`` cannot drift --
``tests/unit/lightspeed/test_seed.py`` asserts every one against the document.
The ids use the vendor's own version-1 UUID layout, written by hand, not
copied from any real example."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

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
]

SEED_RETAILER_ID = "1a000000-0000-1000-8000-000000000001"
SEED_RETAILER_NAME = "Ridgeline Provisions"
SEED_DOMAIN_PREFIX = "unit-lightspeed"
"""Matches the vendor config default, so the token response and the seeded retailer agree."""

SEED_OUTLET_MAIN_ID = "1a000000-0000-1000-8000-000000000101"
SEED_OUTLET_SECOND_ID = "1a000000-0000-1000-8000-000000000102"
"""Two outlets, so the list route has a page boundary to cross."""

SEED_REGISTER_MAIN_ID = "1a000000-0000-1000-8000-000000000201"
SEED_REGISTER_SECOND_ID = "1a000000-0000-1000-8000-000000000202"
"""One register per outlet; two put the documented quota at ``300 x 2 + 50 = 650``."""

SEED_PAYMENT_TYPE_CASH_ID = "1a000000-0000-1000-8000-000000000301"
SEED_PAYMENT_TYPE_CARD_ID = "1a000000-0000-1000-8000-000000000302"
SEED_PAYMENT_TYPE_INTERNAL_ID = "1a000000-0000-1000-8000-000000000303"
"""Three payment types, one ``internal``, so the scope's exclusion rule is testable."""

SEED_PRODUCT_TRAIL_MIX_ID = "1a000000-0000-1000-8000-000000000701"
SEED_PRODUCT_SOCKS_ID = "1a000000-0000-1000-8000-000000000702"
SEED_PRODUCT_BOTTLE_ID = "1a000000-0000-1000-8000-000000000703"
SEED_PRODUCT_TEE_ID = "1a000000-0000-1000-8000-000000000704"
SEED_PRODUCT_TEE_SMALL_ID = "1a000000-0000-1000-8000-000000000705"
SEED_PRODUCT_TEE_LARGE_ID = "1a000000-0000-1000-8000-000000000706"
"""Six products: three standalone plus a tee family, testable via ``?name=``/``?variants=true``."""

SEED_PRODUCT_TRAIL_MIX_SKU = "TRAIL-500"
SEED_PRODUCT_BOTTLE_SKU = "BOTL-1L"
"""Two SKUs for ``?sku=``; the bottle is seeded INACTIVE."""

SEED_CUSTOMER_GROUP_ID = "1a000000-0000-1000-8000-000000000901"
"""The retailer's one customer group; no route creates a second (Customer Groups tag deferred)."""

SEED_CUSTOMER_ADA_ID = "1a000000-0000-1000-8000-000000000911"
SEED_CUSTOMER_BLAKE_ID = "1a000000-0000-1000-8000-000000000912"
SEED_CUSTOMER_NOOR_ID = "1a000000-0000-1000-8000-000000000913"
"""Three customers, one with a null ``last_name`` -- legal, since it is required AND nullable."""

SEED_ADJUSTMENT_REASON_FOUND_ID = "1a000000-0000-1000-8000-000000000921"
SEED_ADJUSTMENT_REASON_SPOILED_ID = "1a000000-0000-1000-8000-000000000922"
"""Two custom adjustment reasons, one POSITIVE one NEGATIVE; the tag that would add a third is deferred."""

SEED_STOCK_ADJUSTMENT_FIRST_ID = "1a000000-0000-1000-8000-000000000931"
SEED_STOCK_ADJUSTMENT_SECOND_ID = "1a000000-0000-1000-8000-000000000932"
"""Two rows already logged, so the list route has a page boundary to cross first."""

SEED_TAX_ID = "1a000000-0000-1000-8000-0000000000a1"
"""The retailer's one tax; no ``taxes`` collection exists, so ``LineItemTax.id`` is never resolved."""

SEED_USER_ID = SEED_RETAILER_ID
"""The cashier every sale names; there is no ``users`` collection, so it reuses the retailer's id."""

SEED_SALE_SAVED_ID = "1a000000-0000-1000-8000-000000000a01"
"""``state: "parked"`` -- a saved sale, still editable. Takes a ``PUT``."""

SEED_SALE_CLOSED_ID = "1a000000-0000-1000-8000-000000000a02"
"""Closed with a payment on the open register, so return and payments-summary
have something to act on; terminal, so ``PUT`` against it 409s."""

SEED_SALE_LAYBY_ID = "1a000000-0000-1000-8000-000000000a03"
"""A layby: parked with the ``layby`` attribute and a part payment -- there is
no ``LAYBY`` state in the 2026-07 schema (see ``machine.py``)."""

SEED_WEBHOOK_ID = "1a000000-0000-1000-8000-000000000401"
SEED_WEBHOOK_TYPE = "register_closure.create"
SEED_WEBHOOK_URL = "https://consumer.example/hooks/lightspeed"
"""One subscription, on the one event this slice can fire."""

SEED_CLIENT_ID = "unit-lightspeed-client-id"
SEED_CLIENT_SECRET = "unit-lightspeed-client-secret"
"""The seeded OAuth app, matching the vendor config default so an unmodified profile still authenticates."""

SEED_ACCESS_TOKEN = "seedfullscopeaccesstoken000000000000001A"
SEED_REFRESH_TOKEN = "seedfullscoperefreshtoken00000000000001A"
"""The pre-issued full-scope OAuth pair; deliberately not UUID-shaped, since the vendor states no token format."""

SEED_READ_ONLY_ACCESS_TOKEN = "seedreadonlyaccesstoken0000000000000001A"
"""Reads only -- every write path (``register:open``/``close``, webhooks) answers 403 to it."""

SEED_PERSONAL_ACCESS_TOKEN = "seedpersonalaccesstoken0000000000000001A"
"""A Plus-plan personal token, full scope, never expires -- only ever arrives from a seed."""

DEFAULT_SEED_PATH: Path = Path(str(resources.files("vendorfake.lightspeed") / "seed" / "default.seed.json"))
"""Resolved through ``importlib.resources`` so it works from a wheel too."""
