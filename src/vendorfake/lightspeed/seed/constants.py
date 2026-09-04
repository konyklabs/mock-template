"""Every id and credential the shipped scenario contains, importable by name.

FOR: a test that wants ``constants.SEED_REGISTER_MAIN_ID`` instead of a UUID
typed into an assertion, and for
:class:`~vendorfake.testing.seeds.LightspeedSeed`, which re-exports these
rather than typing them a second time.

INVARIANT: **these constants and ``default.seed.json`` cannot drift.**
``tests/unit/lightspeed/test_seed.py`` reads the document and asserts every
constant here against it, so a hand-edit to either is a red test rather than a
fixture that quietly stops matching.

The ids are the version-1 UUID layout the vendor's own examples use
(``b1e04bd8-f019-11e3-a0f5-b8ca3a64f8f4``), written by hand in an obviously
sequential pattern so a reader can tell a seeded id from a minted one at a
glance. They are NOT copies of the vendor's example values: a fake that shipped
a real account's ids would be claiming a provenance it does not have.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

__all__ = [
    "DEFAULT_SEED_PATH",
    "SEED_ACCESS_TOKEN",
    "SEED_CLIENT_ID",
    "SEED_CLIENT_SECRET",
    "SEED_CUSTOMER_ID",
    "SEED_DOMAIN_PREFIX",
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
]

SEED_RETAILER_ID = "1a000000-0000-1000-8000-000000000001"
SEED_RETAILER_NAME = "Ridgeline Provisions"
SEED_DOMAIN_PREFIX = "unit-lightspeed"
"""The one retailer. ``domain_prefix`` matches the vendor config's default, so
the token response's own ``domain_prefix`` and the seeded retailer agree."""

SEED_OUTLET_MAIN_ID = "1a000000-0000-1000-8000-000000000101"
SEED_OUTLET_SECOND_ID = "1a000000-0000-1000-8000-000000000102"
"""Two outlets, so the list route has a page boundary to cross."""

SEED_REGISTER_MAIN_ID = "1a000000-0000-1000-8000-000000000201"
SEED_REGISTER_SECOND_ID = "1a000000-0000-1000-8000-000000000202"
"""One register per outlet. Two registers put the documented quota at
``300 x 2 + 50 = 650`` requests per five-minute window."""

SEED_PAYMENT_TYPE_CASH_ID = "1a000000-0000-1000-8000-000000000301"
SEED_PAYMENT_TYPE_CARD_ID = "1a000000-0000-1000-8000-000000000302"
SEED_PAYMENT_TYPE_INTERNAL_ID = "1a000000-0000-1000-8000-000000000303"
"""Three payment types, one of them ``internal`` -- so that "the
``payment_types:read`` scope excludes internal payment types" is testable
rather than merely documented."""

# -- sales, and the minimum they reference (slice L2b of konyklabs/roadmap#94)
# The products and customers here are the few a sale has to resolve against;
# the sibling slice seeds the full collections and these ids stay valid.

SEED_TAX_ID = "1a000000-0000-1000-8000-0000000000a1"
"""The retailer's one tax, already named as both outlets' ``default_tax_id``.
There is no ``taxes`` collection: the Taxes tag is outside issue #94's scoped
surface, so a sale's ``LineItemTax.id`` is carried and never resolved."""

SEED_USER_ID = "1a000000-0000-1000-8000-000000000701"
"""The cashier every seeded sale names as its ``source.author_id``. There is no
``users`` collection either -- the Users tag is out of scope -- so nothing
resolves this id; it exists so the seeded sales carry the same author and a
consumer has a plausible value to send."""

SEED_PRODUCT_COFFEE_ID = "1a000000-0000-1000-8000-000000000801"
SEED_PRODUCT_BEANS_ID = "1a000000-0000-1000-8000-000000000802"
"""Two products, the minimum a sale needs to resolve a line item against."""

SEED_CUSTOMER_ID = "1a000000-0000-1000-8000-000000000901"
"""One customer, so a sale can name one."""

SEED_SALE_SAVED_ID = "1a000000-0000-1000-8000-000000000a01"
"""``state: "parked"`` -- a saved sale, still editable. Takes a ``PUT``."""

SEED_SALE_CLOSED_ID = "1a000000-0000-1000-8000-000000000a02"
"""``state: "closed"`` with a payment on the open main register, so the return
action and the register payments summary both have something real to work on.
Terminal: a ``PUT`` against it is the 409 this vendor's machine produces."""

SEED_SALE_LAYBY_ID = "1a000000-0000-1000-8000-000000000a03"
"""A layby: ``state: "parked"`` carrying the ``layby`` attribute and a part
payment. There is no ``LAYBY`` state in the 2026-07 schema -- see
``machine.py`` -- and ``attributes`` is how the schema expresses one."""

SEED_WEBHOOK_ID = "1a000000-0000-1000-8000-000000000401"
SEED_WEBHOOK_TYPE = "register_closure.create"
SEED_WEBHOOK_URL = "https://consumer.example/hooks/lightspeed"
"""One subscription, on the one event this slice can fire."""

SEED_CLIENT_ID = "unit-lightspeed-client-id"
SEED_CLIENT_SECRET = "unit-lightspeed-client-secret"
"""The seeded OAuth application. The same pair the vendor config defaults to,
so a profile that overrides neither still authenticates."""

SEED_ACCESS_TOKEN = "seedfullscopeaccesstoken000000000000001A"
SEED_REFRESH_TOKEN = "seedfullscoperefreshtoken00000000000001A"
"""The pre-issued OAuth pair: full scopes, and the refresh token that rotates
them. Deliberately not UUID-shaped -- the vendor states no token format and a
consumer must treat one as opaque."""

SEED_READ_ONLY_ACCESS_TOKEN = "seedreadonlyaccesstoken0000000000000001A"
"""Reads only: no ``register:open``, no ``register:close``, no ``webhooks``.
Every write path answers 403 to it."""

SEED_PERSONAL_ACCESS_TOKEN = "seedpersonalaccesstoken0000000000000001A"
"""A personal token -- Plus-plan only, created in the web application, so it
can only ever arrive from a seed. It carries the retailer's full scope set and
never expires."""

DEFAULT_SEED_PATH: Path = Path(str(resources.files("vendorfake.lightspeed") / "seed" / "default.seed.json"))
"""Where the shipped scenario lives, resolved through ``importlib.resources``
so it works from a wheel as well as from a checkout."""
