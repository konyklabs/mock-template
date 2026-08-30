"""Every identifier the shipped seed scenario contains, as importable names.

FOR: giving unit tests, the conformance target and a consumer's own fixtures
one place to learn what is in the default scenario.

INVARIANT: **these constants and ``default.seed.json`` agree.** A test asserts
every name below against the document as shipped.

Guids are lowercase UUIDs, the one documented shape (``ids.py``); the ones
below are readable-on-purpose fakes (a ``...-4000-8000-...`` middle so they
also pass a v4 shape check). The seeded tokens are readable strings rather
than JWTs because a human types them into curl commands (JUDGMENT, the same
call the Clover seed makes); every token the login route mints is JWT-shaped.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "DEFAULT_SEED_PATH",
    "SEED_ACCESS_TOKEN",
    "SEED_CLIENT_ID",
    "SEED_CLIENT_SECRET",
    "SEED_MANAGEMENT_GROUP_GUID",
    "SEED_PARTNER_GUID",
    "SEED_READ_ONLY_ACCESS_TOKEN",
    "SEED_READ_ONLY_SCOPES",
    "SEED_RESTAURANT_GUID",
    "SEED_RESTAURANT_NAME",
    "SEED_SCOPES",
]

DEFAULT_SEED_PATH = Path(__file__).resolve().parent / "default.seed.json"

SEED_RESTAURANT_GUID = "e6a4a8d2-0000-4000-8000-000000000001"
SEED_RESTAURANT_NAME = "Harvest & Rye — Toast"
SEED_MANAGEMENT_GROUP_GUID = "e6a4a8d2-0000-4000-8000-0000000000a1"
"""The restaurant's ``managementGroupGuid`` -- also what the auth adapter
refuses in ``Toast-Restaurant-External-ID`` ("cannot be the GUID of a
restaurant group")."""

SEED_CLIENT_ID = "unit-toast-client-id"
SEED_CLIENT_SECRET = "unit-toast-client-secret"
SEED_PARTNER_GUID = "0f6c1b1e-0000-4000-8000-00000000a0a0"
"""The profiles' ``vendor`` block and ``ToastConfig``'s defaults, restated so a
fixture never spells them twice."""

SEED_ACCESS_TOKEN = "unit-seeded-toast-access-token-full-scopes"
SEED_SCOPES: tuple[str, ...] = (
    "orders:read",
    "orders.channel:read",
    "orders.payments:write",
    "guest.pi:read",
    "delivery_info.address:read",
    "orders:write",
    "menus:read",
    "config:read",
    "restaurants:read",
    "partners:read",
    "stock:read",
    "stock:write",
)
"""The client's full scope set (``config.DEFAULT_SCOPES``)."""

SEED_READ_ONLY_ACCESS_TOKEN = "unit-seeded-toast-access-token-read-only"
SEED_READ_ONLY_SCOPES: tuple[str, ...] = (
    "orders:read",
    "menus:read",
    "config:read",
    "restaurants:read",
    "partners:read",
    "stock:read",
)
"""A second token that cannot write and cannot see guest PII, so the
documented 403 is testable -- and askable by the conformance suite -- without
minting anything."""
