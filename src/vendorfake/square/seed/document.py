"""The seed document's schema, as a model rather than as a cast.

INVARIANT: a scenario is validated before a single entity is inserted. Every model here sets
``extra="forbid"``, and hydration parses the whole document first, so a typo like ``locatoins`` is a
startup failure naming the field, not a unit that silently starts with an empty catalog.

Optional-with-a-default fields are defaulted here rather than at the insertion site, so every default
(``US``, ``America/Los_Angeles``, ...) has one place to find it.

Keys are snake_case, matching every other JSON this build publishes or accepts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "SeedCatalog",
    "SeedDocument",
    "SeedInventoryCount",
    "SeedItem",
    "SeedLineItem",
    "SeedLocation",
    "SeedLoyaltyAccount",
    "SeedLoyaltyProgram",
    "SeedMerchant",
    "SeedMoney",
    "SeedOrder",
    "SeedRewardTier",
    "SeedSubscription",
    "SeedTender",
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
    #: Stated by the scenario, as a location's is, so that two units seeded
    #: from one document publish one ``created_at`` on ``GET /v2/merchants``.
    created_at: str | None = None


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
    """One line of a seeded order. ``base_price_money``, ``name`` and ``variation_name`` are all
    optional because a line naming a ``catalog_object_id`` inherits them from the variation.
    """

    model_config = _SEED

    uid: str = Field(min_length=1)
    quantity: str
    name: str | None = None
    note: str | None = None
    catalog_object_id: str | None = None
    variation_name: str | None = None
    base_price_money: SeedMoney | None = None


class SeedTender(BaseModel):
    """One payment already recorded against a seeded order -- a COMPLETED order must show how it
    was paid, since Square treats it as terminal and fully paid.
    https://developer.squareup.com/reference/square/enums/OrderState

    Field names and the ``CARD`` default follow Square's ``Tender``
    (https://developer.squareup.com/reference/square/objects/Tender); ``location_id``/``transaction_id``
    default to the order's own values during hydration, as PayOrder writes them
    (https://developer.squareup.com/reference/square/orders-api/pay-order).
    """

    model_config = _SEED

    id: str = Field(min_length=1)
    payment_id: str = ""
    amount_money: SeedMoney
    created_at: str | None = None
    location_id: str | None = None
    transaction_id: str | None = None
    type: str = "CARD"


class SeedOrder(BaseModel):
    """An order that already exists when the unit starts. ``closed_at`` and ``tenders`` let a
    terminal order in a scenario be one Square could actually have produced -- without them a
    shipped COMPLETED order is still fully payable and matches no ``closed_at`` filter.
    https://developer.squareup.com/reference/square/objects/Order
    """

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
    closed_at: str | None = None
    version: int = 1
    line_items: tuple[SeedLineItem, ...] = ()
    tenders: tuple[SeedTender, ...] = ()


class SeedCatalogObjectReference(BaseModel):
    """A ``CatalogObjectReference``: an id and the catalog version it was read at.
    https://developer.squareup.com/reference/square/objects/CatalogObjectReference
    """

    model_config = _SEED

    object_id: str | None = None
    catalog_version: int | None = None


class SeedRewardTier(BaseModel):
    """One ``LoyaltyProgramRewardTier``: ``points`` to earn it, a ``name``, and the
    ``pricing_rule_reference`` the published schema REQUIRES.
    https://developer.squareup.com/reference/square/objects/LoyaltyProgramRewardTier

    JUDGMENT: this unit does not model pricing rules (SHRINK), so ``pricing_rule_reference`` is
    carried EMPTY rather than inventing an id that would 404 -- the published schema allows a
    nullable, empty reference (D-006).
    """

    model_config = _SEED

    id: str = Field(min_length=1)
    points: int = Field(gt=0)
    name: str
    pricing_rule_reference: SeedCatalogObjectReference = SeedCatalogObjectReference()
    created_at: str | None = None


class SeedLoyaltyProgram(BaseModel):
    """The seller's one loyalty program (https://developer.squareup.com/reference/square/objects/LoyaltyProgram),
    with a single SPEND accrual rule: buyers earn ``accrual_points`` for every ``spend_amount`` of an order.
    ``location_ids`` defaults to every seeded location.
    https://developer.squareup.com/reference/square/objects/LoyaltyProgramAccrualRule
    """

    model_config = _SEED

    id: str = Field(min_length=1)
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"
    terminology_one: str = "Point"
    terminology_other: str = "Points"
    location_ids: tuple[str, ...] | None = None
    accrual_points: int = Field(default=1, gt=0)
    spend_amount: SeedMoney = SeedMoney(amount=100)
    tax_mode: Literal["BEFORE_TAX", "AFTER_TAX"] = "BEFORE_TAX"
    reward_tiers: tuple[SeedRewardTier, ...] = ()
    created_at: str | None = None


class SeedLoyaltyAccount(BaseModel):
    """A buyer already enrolled, so the search-by-phone path finds someone."""

    model_config = _SEED

    id: str = Field(min_length=1)
    phone_number: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    mapping_id: str | None = None
    balance: int = 0
    lifetime_points: int = 0
    enrolled_at: str | None = None


class SeedInventoryCount(BaseModel):
    """The IN_STOCK quantity of one variation at one location, at a stated
    instant. https://developer.squareup.com/reference/square/objects/InventoryCount"""

    model_config = _SEED

    catalog_object_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    #: A decimal string, as Square sends it.
    quantity: str = Field(min_length=1)
    calculated_at: str | None = None


class SeedToken(BaseModel):
    """A token already issued to the application, letting a consumer skip the
    OAuth dance -- so token validity is not gated by the ``oauth`` capability.
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

    #: Provenance notes; aliased because Pydantic treats a leading underscore
    #: as private, and the key in the file is ``_comment``.
    comment: tuple[str, ...] = Field(default=(), alias="_comment")
    merchant: SeedMerchant
    locations: tuple[SeedLocation, ...] = ()
    catalog: SeedCatalog | None = None
    orders: tuple[SeedOrder, ...] = ()
    loyalty_program: SeedLoyaltyProgram | None = None
    loyalty_accounts: tuple[SeedLoyaltyAccount, ...] = ()
    inventory_counts: tuple[SeedInventoryCount, ...] = ()
    tokens: tuple[SeedToken, ...] = ()
    webhook_subscriptions: tuple[SeedSubscription, ...] = ()


def parse_seed_document(raw: object) -> SeedDocument:
    """Validate a decoded scenario, or raise this vendor's ``internal`` error
    -- a malformed seed misconfigures the *unit* at start, not a consumer
    request. The field path is carried in ``detail``.
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
