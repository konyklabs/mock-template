"""One started Clover unit, driven in process, for the behaviour suites.

Deliberately thin, like the Square harness: it knows the app credentials the
profiles set and how to walk the authorize redirect, because every OAuth test
needs both -- and it seeds nothing. The shipped scenario
(``vendorfake.clover.seed``) is the one source of truth; every id a test
needs is a constant from :mod:`vendorfake.clover.seed.constants`, re-exported
here under the short names the suites use.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from vendorfake import create_unit
from vendorfake.clover.entities import COL, TokenEntity
from vendorfake.clover.seed.constants import (
    CUSTOMER_ADA_ID,
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
    SEED_READ_ONLY_ACCESS_TOKEN,
    SERVICE_CHARGE_DEFAULT_ID,
    TAX_BEVERAGE_ID,
    TAX_BEVERAGE_RATE,
    TAX_DEFAULT_ID,
    TAX_DEFAULT_RATE,
    TENDER_CASH_ID,
    TENDER_EXTERNAL_ID,
)
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import InProcessClient, InProcessResponse, in_process

CLIENT_ID = "UNITCLOVERAPP"
CLIENT_SECRET = "unit-clover-app-secret"
CONFIGURED_REDIRECT_URI = "https://example.test/oauth/callback"
"""The three values the profiles' ``vendor`` block sets."""

MERCHANT_ID = SEED_MERCHANT_ID
ITEM_BEER = ITEM_BEER_ID
ITEM_ESPRESSO = ITEM_ESPRESSO_ID
ITEM_CROISSANT = ITEM_CROISSANT_ID
SEED_ITEMS: tuple[tuple[str, str, int], ...] = (
    (ITEM_BEER, "Craft Beer", 750),
    (ITEM_ESPRESSO, "Espresso", 300),
    (ITEM_CROISSANT, "Croissant", 450),
)
EMPLOYEE_OWNER = EMPLOYEE_OWNER_ID
EMPLOYEE_BARISTA = EMPLOYEE_BARISTA_ID
TENDER_CASH = TENDER_CASH_ID
TENDER_EXTERNAL = TENDER_EXTERNAL_ID
ORDER_TYPE_DINE_IN = ORDER_TYPE_DINE_IN_ID
ORDER_TYPE_TAKEOUT = ORDER_TYPE_TAKE_OUT_ID
SERVICE_CHARGE_DEFAULT = SERVICE_CHARGE_DEFAULT_ID
TAX_DEFAULT = TAX_DEFAULT_ID
TAX_BEVERAGE = TAX_BEVERAGE_ID
MOD_GROUP_MILK = MODIFIER_GROUP_MILK_ID
MOD_OAT = MODIFIER_OAT_ID
MOD_SOY = MODIFIER_SOY_ID
CUSTOMER_ADA = CUSTOMER_ADA_ID
SEED_ORDER = SEED_OPEN_ORDER_ID
__all__ = ["TAX_BEVERAGE_RATE", "TAX_DEFAULT_RATE"]

SEED_META = {"operation_id": "TestSeed", "seed": True}
"""Journal meta for the few entities a test inserts by hand (a restricted
token, an expired one), so they read as scenario state in journal assertions."""


class Silent:
    """A logger that says nothing, so a passing run prints no unit banner."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class Harness:
    """A started unit, the client that drives it, and the seeded bearer."""

    unit: Unit
    api: InProcessClient
    auth: dict[str, str]

    @property
    def read_auth(self) -> dict[str, str]:
        """The seeded token that cannot write."""
        return {"authorization": f"Bearer {SEED_READ_ONLY_ACCESS_TOKEN}"}

    # -- OAuth ---------------------------------------------------------------

    def authorize(self, **query: str) -> InProcessResponse:
        """``GET /oauth/v2/authorize`` with ``client_id`` already filled in."""
        return self.api.call(method="GET", path="/oauth/v2/authorize", query={"client_id": CLIENT_ID, **query})

    def code(self, **query: str) -> str:
        """Walk the authorize redirect and return the authorization code."""
        response = self.authorize(**query)
        location = response.headers["location"]
        return parse_qs(urlsplit(location).query)["code"][0]

    def token(self, **fields: Any) -> InProcessResponse:
        """``POST /oauth/v2/token`` as JSON, ``client_id`` filled in."""
        return self.api.post("/oauth/v2/token", {"client_id": CLIENT_ID, **fields})

    def exchange(self) -> dict[str, Any]:
        """A whole high-trust flow: authorize, exchange, return the 4 fields."""
        response = self.token(client_secret=CLIENT_SECRET, code=self.code())
        assert response.status == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def refresh(self, **fields: Any) -> InProcessResponse:
        """``POST /oauth/v2/refresh`` as JSON, ``client_id`` filled in."""
        return self.api.post("/oauth/v2/refresh", {"client_id": CLIENT_ID, **fields})

    # -- the merchant surface --------------------------------------------------

    def path(self, suffix: str = "") -> str:
        return f"/v3/merchants/{MERCHANT_ID}{suffix}"

    def get(self, suffix: str, **kwargs: Any) -> InProcessResponse:
        return self.api.get(self.path(suffix), headers=self.auth, **kwargs)

    def post(self, suffix: str, body: Any = None, **kwargs: Any) -> InProcessResponse:
        return self.api.post(self.path(suffix), body, headers=self.auth, **kwargs)

    def delete(self, suffix: str, **kwargs: Any) -> InProcessResponse:
        return self.api.delete(self.path(suffix), headers=self.auth, **kwargs)

    def create_order(self, **fields: Any) -> dict[str, Any]:
        response = self.post("/orders", {"currency": "USD", "total": 1500, "state": "open", **fields})
        assert response.status == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def clear_seed_orders(self) -> None:
        """Soft-delete the scenario's open order, for tests that need an
        empty list to reason about."""
        assert self.delete(f"/orders/{SEED_ORDER}").status == 200

    def restricted_token(self, *permissions: str) -> dict[str, str]:
        """A bearer carrying only ``permissions``, inserted as seed state."""
        entity = TokenEntity(
            id=f"tok_restricted_{len(permissions)}",
            access_token=f"restricted-{'-'.join(p.lower() for p in permissions) or 'none'}",
            refresh_token="never-used",
            client_id=CLIENT_ID,
            merchant_id=MERCHANT_ID,
            access_token_expiration_ms=2**53,
            refresh_token_expiration_ms=2**53,
            permissions=permissions,
        )
        self.unit.context.store.collection(COL.tokens).insert(entity.to_entity(), SEED_META)
        return {"authorization": f"Bearer {entity.access_token}"}

    def journal_len(self) -> int:
        return len(self.api.get("/__unit/journal").json()["entries"])


def harness(profile: str = "full", **kwargs: Any) -> Iterator[Harness]:
    """Start a unit on ``profile``, yield it with the seeded bearer, stop it
    however the test ends."""
    unit = create_unit(vendor="clover", profile=profile, logger=Silent(), **kwargs)
    try:
        yield Harness(unit=unit, api=in_process(unit), auth={"authorization": f"Bearer {SEED_ACCESS_TOKEN}"})
    finally:
        unit.stop()
