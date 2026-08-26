"""The seed document's schema, as a model rather than as a cast.

FOR: stating what a scenario file may contain, so that a typo in one is a
startup failure naming the field instead of a unit that starts with an empty
catalog and answers 404 to every read as though the scenario were simply small.

INVARIANT: **a scenario is validated before a single entity is inserted.** The
reference casts the parsed JSON straight to its ``SeedDocument`` interface --
``seed as SeedDocument`` -- so a misspelled ``locatoins`` produces a unit with
no locations and the first symptom is an order that cannot be created. Every
model here sets ``extra="forbid"``, and hydration parses the whole document
first, so nothing is written until everything is known to be readable.

The optional-with-a-default fields are defaulted here rather than at the
insertion site, which is the second thing this file buys: the reference spreads
``?? 'US'`` and ``?? 'America/Los_Angeles'`` through its loader, and a reader
asking "what does a location default to" has to find every one of them.

Keys are snake_case. The reference's copy of this document is camelCase because
its whole entity model is; this build snake_cases every JSON it publishes or
accepts -- profiles, control-plane documents, wire projections -- so a camelCase
scenario would be the only exception. The values, ids and timestamps are
unchanged, so the two documents remain comparable line for line.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "SeedCatalog",
    "SeedDocument",
    "SeedItem",
    "SeedLineItem",
    "SeedLocation",
    "SeedMerchant",
    "SeedMoney",
    "SeedOrder",
    "SeedSubscription",
    "SeedToken",
    "SeedVariation",
    "parse_seed_document",
]

#: Not frozen: nothing mutates a parsed document, but freezing every nested
#: model buys nothing here and costs a confusing error if one day something
#: legitimately wants to normalise a scenario before loading it.
_SEED = ConfigDict(extra="forbid", populate_by_name=True)


class SeedMoney(BaseModel):
    """https://developer.squareup.com/reference/square/objects/Money"""

    model_config = _SEED

    amount: int
    currency: str = "USD"


class SeedMerchant(BaseModel):
    """The seller. Exactly one per scenario."""

    model_config = _SEED

    id: str = Field(min_length=1)
    business_name: str
    country: str = "US"
    language_code: str = "en-US"
    currency: str = "USD"


class SeedLocation(BaseModel):
    """One seller location."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    address: dict[str, str] | None = None
    timezone: str = "America/Los_Angeles"
    capabilities: tuple[str, ...] = ("CREDIT_CARD_PROCESSING",)
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"
    #: Falls back to the merchant's when absent; resolved during hydration,
    #: which is the only place both documents are in hand.
    currency: str | None = None
    country: str | None = None
    language_code: str | None = None
    phone_number: str | None = None
    type: Literal["PHYSICAL", "MOBILE"] = "PHYSICAL"
    created_at: str | None = None


class SeedVariation(BaseModel):
    """An ``ITEM_VARIATION`` under an item."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    price_money: SeedMoney
    pricing_type: Literal["FIXED_PRICING", "VARIABLE_PRICING"] = "FIXED_PRICING"


class SeedItem(BaseModel):
    """A catalog ``ITEM`` and the variations that belong to it."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    description: str | None = None
    updated_at: str | None = None
    #: Square's catalog ``version`` is a millisecond-epoch-shaped int64.
    catalog_version: int = 1_479_335_124_878
    variations: tuple[SeedVariation, ...] = ()


class SeedCatalog(BaseModel):
    model_config = _SEED

    items: tuple[SeedItem, ...] = ()


class SeedLineItem(BaseModel):
    """One line of a seeded order.

    ``base_price_money``, ``name`` and ``variation_name`` are all optional
    because a line naming a ``catalog_object_id`` inherits them from the
    variation, which is what a real CreateOrder does too.
    """

    model_config = _SEED

    uid: str = Field(min_length=1)
    quantity: str
    name: str | None = None
    note: str | None = None
    catalog_object_id: str | None = None
    variation_name: str | None = None
    base_price_money: SeedMoney | None = None


class SeedOrder(BaseModel):
    """An order that already exists when the unit starts."""

    model_config = _SEED

    id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    state: str = "OPEN"
    reference_id: str | None = None
    customer_id: str | None = None
    ticket_name: str | None = None
    source_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    version: int = 1
    line_items: tuple[SeedLineItem, ...] = ()


class SeedToken(BaseModel):
    """A token already issued to the application.

    A scenario with tokens is what lets a consumer who does not care about the
    OAuth dance authenticate immediately -- and why token validity is not gated
    by the ``oauth`` capability.
    """

    model_config = _SEED

    id: str | None = None
    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    client_id: str | None = None
    scopes: tuple[str, ...] = ()
    #: Lifetime from unit start. Defaults to the configured access-token TTL.
    expires_in_ms: int | None = None
    short_lived: bool = False
    flow: Literal["code", "pkce"] = "code"


class SeedSubscription(BaseModel):
    """A webhook subscriber declared by the scenario rather than the profile."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str | None = None
    notification_url: str
    event_types: tuple[str, ...]
    signature_key: str
    enabled: bool = True


class SeedDocument(BaseModel):
    """A whole scenario."""

    model_config = _SEED

    #: Provenance, carried in the document so the file explains itself to
    #: whoever opens it. Aliased because Pydantic treats a leading underscore
    #: as a private attribute, and the key in the file is ``_comment``.
    comment: tuple[str, ...] = Field(default=(), alias="_comment")
    merchant: SeedMerchant
    locations: tuple[SeedLocation, ...] = ()
    catalog: SeedCatalog | None = None
    orders: tuple[SeedOrder, ...] = ()
    tokens: tuple[SeedToken, ...] = ()
    webhook_subscriptions: tuple[SeedSubscription, ...] = ()


def parse_seed_document(raw: object) -> SeedDocument:
    """Validate a decoded scenario, or raise this vendor's ``internal`` error.

    ``internal`` rather than ``invalid_value``: a malformed seed is a
    misconfiguration of the *unit*, discovered at start, and there is no
    consumer request to blame it on. The field path is carried in ``detail`` so
    the operator can see which key is wrong.
    """
    if raw is None:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail=("No seed scenario was supplied. Name one in the profile's `seed` field or in VENDORFAKE_SEED."),
        )
    try:
        return SeedDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "(document root)"
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail=f"The seed scenario is not valid at {where}: {first.get('msg', 'invalid')}.",
            info={"errors": exc.error_count(), "field": where},
        ) from exc


def money_entity(money: SeedMoney) -> dict[str, Any]:
    """A seed money value as the store holds it."""
    return {"amount": money.amount, "currency": money.currency}
