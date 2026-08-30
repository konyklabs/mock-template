"""The seed scenario: what a toast unit's world looks like at start.

One restaurant ("Harvest & Rye — Toast"), its partner connection, two
pre-minted bearers (full-scope and read-only), the configuration lists an
ordering integration reads, and a V3 menu with the documented 8.99 item.
:mod:`.constants` names every id; :mod:`.document` is the schema;
:mod:`.hydrate` loads it.
"""

from __future__ import annotations

from vendorfake.toast.seed import constants
from vendorfake.toast.seed.constants import (
    DEFAULT_SEED_PATH,
    SEED_ACCESS_TOKEN,
    SEED_CLIENT_ID,
    SEED_CLIENT_SECRET,
    SEED_MANAGEMENT_GROUP_GUID,
    SEED_PARTNER_GUID,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_READ_ONLY_SCOPES,
    SEED_RESTAURANT_GUID,
    SEED_RESTAURANT_NAME,
    SEED_SCOPES,
)
from vendorfake.toast.seed.document import SeedDocument, parse_seed_document
from vendorfake.toast.seed.hydrate import SEED_META, hydrate_toast

__all__ = [
    "DEFAULT_SEED_PATH",
    "SEED_ACCESS_TOKEN",
    "SEED_CLIENT_ID",
    "SEED_CLIENT_SECRET",
    "SEED_MANAGEMENT_GROUP_GUID",
    "SEED_META",
    "SEED_PARTNER_GUID",
    "SEED_READ_ONLY_ACCESS_TOKEN",
    "SEED_READ_ONLY_SCOPES",
    "SEED_RESTAURANT_GUID",
    "SEED_RESTAURANT_NAME",
    "SEED_SCOPES",
    "SeedDocument",
    "constants",
    "hydrate_toast",
    "parse_seed_document",
]
