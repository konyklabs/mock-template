"""One started Clover unit, driven in process, for the behaviour suites.

Deliberately thin, like the Square harness: it knows the app credentials the
``full`` profile sets and how to walk the authorize redirect, because every
OAuth test needs both.

Until PR E ships the seed scenario, the store starts empty, so the harness
inserts the scenario the surfaces need -- one merchant and three inventory
items ("Craft Beer" at 750 is the documented create-item example) -- marked
``{"seed": True}`` in their journal meta the way real seed writes are, so
they never read as request traffic in a journal assertion. A full-permission
bearer token is minted through the real OAuth flow at start and offered as
``auth``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from vendorfake import create_unit
from vendorfake.clover.entities import COL, ItemEntity, MerchantEntity, TokenEntity
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import InProcessClient, InProcessResponse, in_process

CLIENT_ID = "UNITCLOVERAPP"
CLIENT_SECRET = "unit-clover-app-secret"
CONFIGURED_REDIRECT_URI = "https://example.test/oauth/callback"
"""The three values ``profiles/full.json`` sets."""

MERCHANT_ID = "HRVSTRYE12345"
"""13 uppercase characters, matching the entity-id shape."""

ITEM_BEER = "CRAFTBEER0750"
ITEM_ESPRESSO = "ESPRESSO00300"
ITEM_CROISSANT = "CROISSANT0450"
SEED_ITEMS: tuple[tuple[str, str, int], ...] = (
    (ITEM_BEER, "Craft Beer", 750),
    (ITEM_ESPRESSO, "Espresso", 300),
    (ITEM_CROISSANT, "Croissant", 450),
)

EMPLOYEE_OWNER = "OWNERHRVST001"
EMPLOYEE_BARISTA = "EMPLBARISTA01"
TENDER_CASH = "TENDERCASH001"
TENDER_EXTERNAL = "TENDEREXTRN01"
ORDER_TYPE_DINE_IN = "KFRPRVCZ73JHM"
ORDER_TYPE_TAKEOUT = "ORDTYPETAKE01"
SERVICE_CHARGE_DEFAULT = "SVCCHARGE0001"
TAX_DEFAULT = "TAXDEFAULT001"
TAX_DEFAULT_RATE = 725000
"""7.25% at the JUDGMENT scale of percent x 100000 (model/order.py)."""
TAX_BEVERAGE = "TAXBEVERAGE01"
TAX_BEVERAGE_RATE = 1000000
"""10%."""
MOD_GROUP_MILK = "MODGROUPMILK1"
MOD_OAT = "MODIFIEROAT01"
MOD_SOY = "MODIFIERSOY01"
CUSTOMER_ADA = "CUSTOMERADA01"

SEED_META = {"operation_id": "TestSeed", "seed": True}


class Silent:
    """A logger that says nothing, so a passing run prints no unit banner."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class Harness:
    """A started unit, the client that drives it, and a live bearer."""

    unit: Unit
    api: InProcessClient
    auth: dict[str, str]

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


def seed(unit: Unit) -> None:
    """The scenario the surfaces need, until PR E ships the real one."""
    store = unit.context.store
    store.collection(COL.merchants).insert(
        MerchantEntity(
            id=MERCHANT_ID,
            name="Harvest & Rye",
            currency="USD",
            owner={"id": "OWNERHRVST001", "name": "R. Harvest"},
            address={"address1": "1 Main St", "city": "Springfield", "state": "IL", "zip": "62701", "country": "US"},
        ).to_entity(),
        SEED_META,
    )
    # Tax rates: the merchant default (isDefault) and a beverage rate an item
    # opts into explicitly with defaultTaxRates=false.
    store.collection(COL.tax_rates).insert(
        {"id": TAX_DEFAULT, "name": "Sales Tax", "rate": TAX_DEFAULT_RATE, "isDefault": True}, SEED_META
    )
    store.collection(COL.tax_rates).insert(
        {"id": TAX_BEVERAGE, "name": "Beverage Tax", "rate": TAX_BEVERAGE_RATE, "isDefault": False}, SEED_META
    )
    # Items: beer opts into the beverage rate; espresso carries the milk
    # modifier group; croissant takes the merchant defaults.
    store.collection(COL.items).insert(
        ItemEntity(
            id=ITEM_BEER,
            name="Craft Beer",
            price=750,
            modifiedTime=1755786102000,
            defaultTaxRates=False,
            taxRates=({"id": TAX_BEVERAGE},),
        ).to_entity(),
        SEED_META,
    )
    store.collection(COL.items).insert(
        ItemEntity(
            id=ITEM_ESPRESSO, name="Espresso", price=300, modifiedTime=1755786102000, modifierGroupIds=(MOD_GROUP_MILK,)
        ).to_entity(),
        SEED_META,
    )
    store.collection(COL.items).insert(
        ItemEntity(id=ITEM_CROISSANT, name="Croissant", price=450, modifiedTime=1755786102000).to_entity(),
        SEED_META,
    )
    store.collection(COL.modifier_groups).insert(
        {"id": MOD_GROUP_MILK, "name": "Milk", "showByDefault": True, "modifierIds": [MOD_OAT, MOD_SOY]},
        SEED_META,
    )
    store.collection(COL.modifiers).insert(
        {"id": MOD_OAT, "name": "Oat milk", "price": 50, "available": True, "modifierGroup": {"id": MOD_GROUP_MILK}},
        SEED_META,
    )
    store.collection(COL.modifiers).insert(
        {"id": MOD_SOY, "name": "Soy milk", "price": 50, "available": False, "modifierGroup": {"id": MOD_GROUP_MILK}},
        SEED_META,
    )
    store.collection(COL.employees).insert(
        {"id": EMPLOYEE_OWNER, "name": "R. Harvest", "nickname": "Rye", "role": "ADMIN", "isOwner": True}, SEED_META
    )
    store.collection(COL.employees).insert(
        {"id": EMPLOYEE_BARISTA, "name": "B. Rista", "role": "EMPLOYEE", "isOwner": False}, SEED_META
    )
    store.collection(COL.tenders).insert(
        {
            "id": TENDER_CASH,
            "label": "Cash",
            "labelKey": "com.clover.tender.cash",
            "enabled": True,
            "visible": True,
            "opensCashDrawer": True,
            "editable": False,
        },
        SEED_META,
    )
    store.collection(COL.tenders).insert(
        {
            "id": TENDER_EXTERNAL,
            "label": "Online Order",
            "labelKey": "com.clover.tender.external_payment",
            "enabled": True,
            "visible": True,
            "opensCashDrawer": False,
            "editable": True,
        },
        SEED_META,
    )
    store.collection(COL.order_types).insert(
        {
            "id": ORDER_TYPE_DINE_IN,
            "label": "Dine In",
            "labelKey": "com.clover.order_type.dine_in",
            "taxable": True,
            "isDefault": True,
            "filterCategories": False,
            "isHidden": False,
            "fee": 0,
        },
        SEED_META,
    )
    store.collection(COL.order_types).insert(
        {
            "id": ORDER_TYPE_TAKEOUT,
            "label": "Take Out",
            "labelKey": "com.clover.order_type.take_out",
            "taxable": True,
            "isDefault": False,
            "filterCategories": False,
            "isHidden": False,
            "fee": 0,
        },
        SEED_META,
    )
    store.collection(COL.service_charges).insert(
        {
            "id": SERVICE_CHARGE_DEFAULT,
            "name": "Service",
            "percentageDecimal": 180000,
            "enabled": True,
            "isDefault": True,
        },
        SEED_META,
    )
    store.collection(COL.customers).insert(
        {
            "id": CUSTOMER_ADA,
            "firstName": "Ada",
            "lastName": "Lovelace",
            "customerSince": 1755786102000,
            "addresses": [{"address1": "12 Analytical Way", "city": "London", "country": "GB"}],
        },
        SEED_META,
    )


def harness(profile: str = "full", **kwargs: Any) -> Iterator[Harness]:
    """Start a unit with the scenario seeded and a bearer minted; stop it after."""
    unit = create_unit(vendor="clover", profile=profile, logger=Silent(), **kwargs)
    seed(unit)
    api = in_process(unit)
    bare = Harness(unit=unit, api=api, auth={})
    try:
        token = bare.exchange()
        yield Harness(unit=unit, api=api, auth={"authorization": f"Bearer {token['access_token']}"})
    finally:
        unit.stop()
