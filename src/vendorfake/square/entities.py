"""The shapes this vendor stores, and the collections it stores them in.

Each surface gets one typed reading of every stored entity, so a stored
field's name is written once instead of re-spelled as a dict key everywhere
it's touched. The stored model is this unit's own; the projections in
:mod:`.model` translate it to Square's wire JSON, so a field Square renames
is a one-line change in a projector, not a rename across the state engine.

INVARIANT: absence is absence -- a field never set is *missing*, never ``None``. ``to_entity()`` drops
unset optionals via ``compact()``; the digest, the journal, and the wire projection all depend on it.

JUDGMENT: collection names are snake_case throughout, including the two this
project's model would otherwise camelCase, for consistency with the rest of
this build's vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact

__all__ = [
    "COL",
    "AuthorizationCodeEntity",
    "CatalogObjectEntity",
    "Fulfillment",
    "InventoryCountEntity",
    "LocationEntity",
    "LoyaltyAccountEntity",
    "LoyaltyEventEntity",
    "LoyaltyProgramEntity",
    "MerchantEntity",
    "Money",
    "OrderEntity",
    "OrderLineItem",
    "PaymentEntity",
    "SquareCollections",
    "Tender",
    "TokenEntity",
    "inventory_count_id",
]


@dataclass(frozen=True, slots=True)
class SquareCollections:
    """The store collections this vendor uses, named once."""

    merchants: str = "merchants"
    locations: str = "locations"
    catalog: str = "catalog_objects"
    orders: str = "orders"
    payments: str = "payments"
    loyalty_programs: str = "loyalty_programs"
    loyalty_accounts: str = "loyalty_accounts"
    loyalty_events: str = "loyalty_events"
    inventory_counts: str = "inventory_counts"
    codes: str = "authorization_codes"
    tokens: str = "tokens"

    def names(self) -> tuple[str, ...]:
        """Every collection name, in declaration order."""
        return (
            self.merchants,
            self.locations,
            self.catalog,
            self.orders,
            self.payments,
            self.loyalty_programs,
            self.loyalty_accounts,
            self.loyalty_events,
            self.inventory_counts,
            self.codes,
            self.tokens,
        )


COL = SquareCollections()
"""The one place a collection name is spelled."""


# ---------------------------------------------------------------------------
# Readers: tolerant on type, strict on presence -- a wrong type here is a
# defect in this package, not bad input.
# ---------------------------------------------------------------------------


def _str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _int(value: Any, default: int = 0) -> int:
    return default if not isinstance(value, int) or isinstance(value, bool) else value


def _bool(value: Any, default: bool = False) -> bool:
    return default if value is None else bool(value)


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value)
    return ()


def _mapping(value: Any) -> dict[str, str] | None:
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    return None


@dataclass(frozen=True, slots=True)
class Money:
    """Square's ``Money``: minor units plus a currency code.
    https://developer.squareup.com/reference/square/objects/Money"""

    amount: int
    currency: str

    @classmethod
    def from_entity(cls, value: Any) -> Money | None:
        """Read a stored money object, or ``None`` when there is none."""
        if not isinstance(value, Mapping):
            return None
        return cls(amount=_int(value.get("amount")), currency=_str(value.get("currency")))

    def to_entity(self) -> dict[str, Any]:
        return {"amount": self.amount, "currency": self.currency}


@dataclass(frozen=True, slots=True)
class MerchantEntity:
    """The seller. One per unit, in practice."""

    id: str
    business_name: str
    country: str = "US"
    language_code: str = "en-US"
    currency: str = "USD"
    status: str = "ACTIVE"

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> MerchantEntity:
        return cls(
            id=_str(entity["id"]),
            business_name=_str(entity.get("business_name")),
            country=_str(entity.get("country"), "US"),
            language_code=_str(entity.get("language_code"), "en-US"),
            currency=_str(entity.get("currency"), "USD"),
            status=_str(entity.get("status"), "ACTIVE"),
        )

    def to_entity(self) -> Entity:
        return {
            "id": self.id,
            "business_name": self.business_name,
            "country": self.country,
            "language_code": self.language_code,
            "currency": self.currency,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class LocationEntity:
    """A seller location -- the reference data an order points at."""

    id: str
    merchant_id: str
    name: str
    business_name: str
    timezone: str = "America/Los_Angeles"
    capabilities: tuple[str, ...] = ("CREDIT_CARD_PROCESSING",)
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"
    country: str = "US"
    language_code: str = "en-US"
    currency: str = "USD"
    type: Literal["PHYSICAL", "MOBILE"] = "PHYSICAL"
    address: dict[str, str] | None = None
    phone_number: str | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> LocationEntity:
        status = _str(entity.get("status"), "ACTIVE")
        kind = _str(entity.get("type"), "PHYSICAL")
        return cls(
            id=_str(entity["id"]),
            merchant_id=_str(entity.get("merchant_id")),
            name=_str(entity.get("name")),
            business_name=_str(entity.get("business_name")),
            timezone=_str(entity.get("timezone"), "America/Los_Angeles"),
            capabilities=_str_tuple(entity.get("capabilities")) or ("CREDIT_CARD_PROCESSING",),
            status="INACTIVE" if status == "INACTIVE" else "ACTIVE",
            country=_str(entity.get("country"), "US"),
            language_code=_str(entity.get("language_code"), "en-US"),
            currency=_str(entity.get("currency"), "USD"),
            type="MOBILE" if kind == "MOBILE" else "PHYSICAL",
            address=_mapping(entity.get("address")),
            phone_number=_opt_str(entity.get("phone_number")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "merchant_id": self.merchant_id,
                "name": self.name,
                "business_name": self.business_name,
                "timezone": self.timezone,
                "capabilities": list(self.capabilities),
                "status": self.status,
                "country": self.country,
                "language_code": self.language_code,
                "currency": self.currency,
                "type": self.type,
                "address": self.address,
                "phone_number": self.phone_number,
            }
        )


@dataclass(frozen=True, slots=True)
class CatalogObjectEntity:
    """An ITEM or an ITEM_VARIATION; one collection holds both, as Square does.
    https://developer.squareup.com/reference/square/objects/CatalogObject"""

    id: str
    object_type: Literal["ITEM", "ITEM_VARIATION"]
    #: Square's catalog ``version`` is a millisecond-epoch-shaped int64.
    catalog_version: int = 1_479_335_124_878
    is_deleted: bool = False
    present_at_all_locations: bool = True
    item_name: str | None = None
    item_description: str | None = None
    #: ITEM_VARIATION only: the ITEM it belongs to.
    item_id: str | None = None
    variation_name: str | None = None
    pricing_type: Literal["FIXED_PRICING", "VARIABLE_PRICING"] | None = None
    price_money: Money | None = None

    @property
    def is_variation(self) -> bool:
        return self.object_type == "ITEM_VARIATION"

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> CatalogObjectEntity:
        kind = _str(entity.get("object_type"), "ITEM")
        pricing = _opt_str(entity.get("pricing_type"))
        return cls(
            id=_str(entity["id"]),
            object_type="ITEM_VARIATION" if kind == "ITEM_VARIATION" else "ITEM",
            catalog_version=_int(entity.get("catalog_version"), 1_479_335_124_878),
            is_deleted=_bool(entity.get("is_deleted")),
            present_at_all_locations=_bool(entity.get("present_at_all_locations"), True),
            item_name=_opt_str(entity.get("item_name")),
            item_description=_opt_str(entity.get("item_description")),
            item_id=_opt_str(entity.get("item_id")),
            variation_name=_opt_str(entity.get("variation_name")),
            pricing_type="VARIABLE_PRICING"
            if pricing == "VARIABLE_PRICING"
            else ("FIXED_PRICING" if pricing else None),
            price_money=Money.from_entity(entity.get("price_money")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "object_type": self.object_type,
                "catalog_version": self.catalog_version,
                "is_deleted": self.is_deleted,
                "present_at_all_locations": self.present_at_all_locations,
                "item_name": self.item_name,
                "item_description": self.item_description,
                "item_id": self.item_id,
                "variation_name": self.variation_name,
                "pricing_type": self.pricing_type,
                "price_money": None if self.price_money is None else self.price_money.to_entity(),
            }
        )


@dataclass(frozen=True, slots=True)
class OrderLineItem:
    """One line of an order, nested inside the order entity, not a
    collection. ``quantity`` is a **string**, as Square sends it
    (https://developer.squareup.com/reference/square/objects/OrderLineItem)."""

    uid: str
    quantity: str
    base_price_money: Money
    name: str | None = None
    note: str | None = None
    catalog_object_id: str | None = None
    variation_name: str | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> OrderLineItem:
        price = Money.from_entity(entity.get("base_price_money"))
        return cls(
            uid=_str(entity.get("uid")),
            quantity=_str(entity.get("quantity"), "1"),
            base_price_money=Money(amount=0, currency="USD") if price is None else price,
            name=_opt_str(entity.get("name")),
            note=_opt_str(entity.get("note")),
            catalog_object_id=_opt_str(entity.get("catalog_object_id")),
            variation_name=_opt_str(entity.get("variation_name")),
        )

    def to_entity(self) -> dict[str, Any]:
        return compact(
            {
                "uid": self.uid,
                "quantity": self.quantity,
                "base_price_money": self.base_price_money.to_entity(),
                "name": self.name,
                "note": self.note,
                "catalog_object_id": self.catalog_object_id,
                "variation_name": self.variation_name,
            }
        )


@dataclass(frozen=True, slots=True)
class Tender:
    """One payment against an order. https://developer.squareup.com/reference/square/objects/Tender

    JUDGMENT: ``type`` defaults to ``CARD`` (a real ``TenderType``,
    https://developer.squareup.com/reference/square/enums/TenderType, matching Square's PayOrder example,
    https://developer.squareup.com/reference/square/orders-api/pay-order) since this unit has no Payments API.
    """

    id: str
    location_id: str
    transaction_id: str
    created_at: str
    #: "The total amount of the tender, including `tip_money`."
    amount_money: Money
    type: str = "CARD"
    payment_id: str = ""
    #: "The tip's amount of the tender." Absent when no tip was taken.
    tip_money: Money | None = None

    @property
    def applied(self) -> int:
        """What this tender pays toward the order: the amount less the tip."""
        return self.amount_money.amount - (0 if self.tip_money is None else self.tip_money.amount)

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> Tender:
        amount = Money.from_entity(entity.get("amount_money"))
        return cls(
            id=_str(entity.get("id")),
            location_id=_str(entity.get("location_id")),
            transaction_id=_str(entity.get("transaction_id")),
            created_at=_str(entity.get("created_at")),
            amount_money=Money(amount=0, currency="USD") if amount is None else amount,
            type=_str(entity.get("type"), "CARD"),
            payment_id=_str(entity.get("payment_id")),
            tip_money=Money.from_entity(entity.get("tip_money")),
        )

    def to_entity(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "location_id": self.location_id,
                "transaction_id": self.transaction_id,
                "created_at": self.created_at,
                "amount_money": self.amount_money.to_entity(),
                "type": self.type,
                "payment_id": self.payment_id,
                "tip_money": None if self.tip_money is None else self.tip_money.to_entity(),
            }
        )


@dataclass(frozen=True, slots=True)
class Fulfillment:
    """One fulfillment of an order: how the buyer receives it -- ``uid``, ``type``
    (PICKUP/SHIPMENT/DELIVERY), ``state``, and one details object named for the type.
    https://developer.squareup.com/reference/square/objects/Fulfillment https://developer.squareup.com/reference/square/enums/FulfillmentState

    ``supplied_stamps`` is stored and digested but never on the wire -- a ``(name, value)``
    pair sequence, not a mapping, since the digest must scrub a volatile stamp name at any
    depth (see ``surface/orders.py``, "Stamps and the digest").
    """

    uid: str
    type: str
    state: str = "PROPOSED"
    line_item_application: str = "ALL"
    pickup_details: dict[str, Any] | None = None
    delivery_details: dict[str, Any] | None = None
    shipment_details: dict[str, Any] | None = None
    supplied_stamps: tuple[tuple[str, Any], ...] | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> Fulfillment:
        return cls(
            uid=_str(entity.get("uid")),
            type=_str(entity.get("type"), "PICKUP"),
            state=_str(entity.get("state"), "PROPOSED"),
            line_item_application=_str(entity.get("line_item_application"), "ALL"),
            pickup_details=_details(entity.get("pickup_details")),
            delivery_details=_details(entity.get("delivery_details")),
            shipment_details=_details(entity.get("shipment_details")),
            supplied_stamps=_pairs(entity.get("supplied_stamps")),
        )

    def to_entity(self) -> dict[str, Any]:
        return compact(
            {
                "uid": self.uid,
                "type": self.type,
                "state": self.state,
                "line_item_application": self.line_item_application,
                "pickup_details": self.pickup_details,
                "delivery_details": self.delivery_details,
                "shipment_details": self.shipment_details,
                "supplied_stamps": [list(pair) for pair in self.supplied_stamps] if self.supplied_stamps else None,
            }
        )


def _details(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return None


def _pairs(value: Any) -> tuple[tuple[str, Any], ...] | None:
    """A stored ``[[name, value], ...]`` list back as pairs; anything else is absent."""
    if isinstance(value, list) and value:
        return tuple((str(pair[0]), pair[1]) for pair in value if isinstance(pair, list | tuple) and len(pair) == 2)
    return None


@dataclass(frozen=True, slots=True)
class OrderEntity:
    """An order, as stored. ``version``, ``created_at`` and ``updated_at`` are the store's,
    matching Square's documented read-only field. https://developer.squareup.com/reference/square/objects/Order
    """

    id: str
    location_id: str
    merchant_id: str
    currency: str
    state: str = "OPEN"
    line_items: tuple[OrderLineItem, ...] = ()
    tenders: tuple[Tender, ...] = ()
    fulfillments: tuple[Fulfillment, ...] = ()
    reference_id: str | None = None
    customer_id: str | None = None
    source_name: str | None = None
    ticket_name: str | None = None
    closed_at: str | None = None
    metadata: dict[str, str] | None = None
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> OrderEntity:
        raw_lines = entity.get("line_items")
        raw_tenders = entity.get("tenders")
        raw_fulfillments = entity.get("fulfillments")
        return cls(
            id=_str(entity["id"]),
            location_id=_str(entity.get("location_id")),
            merchant_id=_str(entity.get("merchant_id")),
            currency=_str(entity.get("currency"), "USD"),
            state=_str(entity.get("state"), "OPEN"),
            line_items=tuple(
                OrderLineItem.from_entity(item)
                for item in (raw_lines if isinstance(raw_lines, Sequence) and not isinstance(raw_lines, str) else ())
                if isinstance(item, Mapping)
            ),
            tenders=tuple(
                Tender.from_entity(item)
                for item in (
                    raw_tenders if isinstance(raw_tenders, Sequence) and not isinstance(raw_tenders, str) else ()
                )
                if isinstance(item, Mapping)
            ),
            fulfillments=tuple(
                Fulfillment.from_entity(item)
                for item in (
                    raw_fulfillments
                    if isinstance(raw_fulfillments, Sequence) and not isinstance(raw_fulfillments, str)
                    else ()
                )
                if isinstance(item, Mapping)
            ),
            reference_id=_opt_str(entity.get("reference_id")),
            customer_id=_opt_str(entity.get("customer_id")),
            source_name=_opt_str(entity.get("source_name")),
            ticket_name=_opt_str(entity.get("ticket_name")),
            closed_at=_opt_str(entity.get("closed_at")),
            metadata=_mapping(entity.get("metadata")),
            version=_int(entity.get("version"), 1),
            created_at=_str(entity.get("created_at")),
            updated_at=_str(entity.get("updated_at")),
        )

    def to_entity(self) -> Entity:
        """``created_at``/``updated_at`` are omitted when empty so
        ``Collection.insert`` fills them from the clock; a seeded order that
        states them keeps its own."""
        return compact(
            {
                "id": self.id,
                "location_id": self.location_id,
                "merchant_id": self.merchant_id,
                "currency": self.currency,
                "state": self.state,
                "line_items": [item.to_entity() for item in self.line_items],
                "tenders": [tender.to_entity() for tender in self.tenders],
                "fulfillments": [fulfillment.to_entity() for fulfillment in self.fulfillments] or None,
                "reference_id": self.reference_id,
                "customer_id": self.customer_id,
                "source_name": self.source_name,
                "ticket_name": self.ticket_name,
                "closed_at": self.closed_at,
                "metadata": self.metadata,
                "version": self.version,
                "created_at": self.created_at or None,
                "updated_at": self.updated_at or None,
            }
        )


@dataclass(frozen=True, slots=True)
class PaymentEntity:
    """A payment, as stored. https://developer.squareup.com/reference/square/objects/Payment --
    only the ``EXTERNAL`` source is modelled, so ``source_type`` is always ``EXTERNAL`` here.
    """

    id: str
    location_id: str
    merchant_id: str
    amount_money: Money
    status: str = "APPROVED"
    source_type: str = "EXTERNAL"
    tip_money: Money | None = None
    order_id: str | None = None
    customer_id: str | None = None
    reference_id: str | None = None
    note: str | None = None
    external_type: str | None = None
    external_source: str | None = None
    external_source_id: str | None = None
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> PaymentEntity:
        amount = Money.from_entity(entity.get("amount_money"))
        return cls(
            id=_str(entity["id"]),
            location_id=_str(entity.get("location_id")),
            merchant_id=_str(entity.get("merchant_id")),
            amount_money=Money(amount=0, currency="USD") if amount is None else amount,
            status=_str(entity.get("status"), "APPROVED"),
            source_type=_str(entity.get("source_type"), "EXTERNAL"),
            tip_money=Money.from_entity(entity.get("tip_money")),
            order_id=_opt_str(entity.get("order_id")),
            customer_id=_opt_str(entity.get("customer_id")),
            reference_id=_opt_str(entity.get("reference_id")),
            note=_opt_str(entity.get("note")),
            external_type=_opt_str(entity.get("external_type")),
            external_source=_opt_str(entity.get("external_source")),
            external_source_id=_opt_str(entity.get("external_source_id")),
            version=_int(entity.get("version"), 1),
            created_at=_str(entity.get("created_at")),
            updated_at=_str(entity.get("updated_at")),
        )

    @property
    def total(self) -> int:
        """``total_money``: the amount plus the tip, in minor units."""
        return self.amount_money.amount + (0 if self.tip_money is None else self.tip_money.amount)

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "location_id": self.location_id,
                "merchant_id": self.merchant_id,
                "amount_money": self.amount_money.to_entity(),
                "tip_money": None if self.tip_money is None else self.tip_money.to_entity(),
                "status": self.status,
                "source_type": self.source_type,
                "order_id": self.order_id,
                "customer_id": self.customer_id,
                "reference_id": self.reference_id,
                "note": self.note,
                "external_type": self.external_type,
                "external_source": self.external_source,
                "external_source_id": self.external_source_id,
                "version": self.version,
                "created_at": self.created_at or None,
                "updated_at": self.updated_at or None,
            }
        )


@dataclass(frozen=True, slots=True)
class LoyaltyProgramEntity:
    """The seller's loyalty program. One per unit, in practice.
    https://developer.squareup.com/reference/square/objects/LoyaltyProgram.
    A single SPEND accrual rule is modelled; reward tiers are stored as the
    documents the seed gave, projected as they are.
    """

    id: str
    merchant_id: str
    status: str = "ACTIVE"
    terminology_one: str = "Point"
    terminology_other: str = "Points"
    location_ids: tuple[str, ...] = ()
    accrual_points: int = 1
    spend_amount: Money = Money(amount=100, currency="USD")
    tax_mode: str = "BEFORE_TAX"
    reward_tiers: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> LoyaltyProgramEntity:
        spend = Money.from_entity(entity.get("spend_amount"))
        tiers = entity.get("reward_tiers")
        return cls(
            id=_str(entity["id"]),
            merchant_id=_str(entity.get("merchant_id")),
            status=_str(entity.get("status"), "ACTIVE"),
            terminology_one=_str(entity.get("terminology_one"), "Point"),
            terminology_other=_str(entity.get("terminology_other"), "Points"),
            location_ids=_str_tuple(entity.get("location_ids")),
            accrual_points=_int(entity.get("accrual_points"), 1),
            spend_amount=Money(amount=100, currency="USD") if spend is None else spend,
            tax_mode=_str(entity.get("tax_mode"), "BEFORE_TAX"),
            reward_tiers=tuple(
                dict(tier)
                for tier in (tiers if isinstance(tiers, Sequence) and not isinstance(tiers, str) else ())
                if isinstance(tier, Mapping)
            ),
        )

    def to_entity(self) -> Entity:
        return {
            "id": self.id,
            "merchant_id": self.merchant_id,
            "status": self.status,
            "terminology_one": self.terminology_one,
            "terminology_other": self.terminology_other,
            "location_ids": list(self.location_ids),
            "accrual_points": self.accrual_points,
            "spend_amount": self.spend_amount.to_entity(),
            "tax_mode": self.tax_mode,
            "reward_tiers": [dict(tier) for tier in self.reward_tiers],
        }


@dataclass(frozen=True, slots=True)
class LoyaltyAccountEntity:
    """A buyer's account, keyed to a phone number.
    https://developer.squareup.com/reference/square/objects/LoyaltyAccount --
    ``mapping_created_at`` is the phone-mapping's own timestamp, distinct from ``enrolled_at``.
    """

    id: str
    program_id: str
    customer_id: str
    phone_number: str
    mapping_id: str
    balance: int = 0
    lifetime_points: int = 0
    enrolled_at: str = ""
    mapping_created_at: str = ""

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> LoyaltyAccountEntity:
        return cls(
            id=_str(entity["id"]),
            program_id=_str(entity.get("program_id")),
            customer_id=_str(entity.get("customer_id")),
            phone_number=_str(entity.get("phone_number")),
            mapping_id=_str(entity.get("mapping_id")),
            balance=_int(entity.get("balance")),
            lifetime_points=_int(entity.get("lifetime_points")),
            enrolled_at=_str(entity.get("enrolled_at")),
            mapping_created_at=_str(entity.get("mapping_created_at")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "program_id": self.program_id,
                "customer_id": self.customer_id,
                "phone_number": self.phone_number,
                "mapping_id": self.mapping_id,
                "balance": self.balance,
                "lifetime_points": self.lifetime_points,
                "enrolled_at": self.enrolled_at or None,
                "mapping_created_at": self.mapping_created_at or None,
            }
        )


@dataclass(frozen=True, slots=True)
class LoyaltyEventEntity:
    """One ledger entry against an account. Only ``ACCUMULATE_POINTS`` is
    minted here. https://developer.squareup.com/reference/square/objects/LoyaltyEvent"""

    id: str
    type: str
    account_id: str
    program_id: str
    location_id: str
    points: int
    order_id: str | None = None
    source: str = "LOYALTY_API"

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> LoyaltyEventEntity:
        return cls(
            id=_str(entity["id"]),
            type=_str(entity.get("type"), "ACCUMULATE_POINTS"),
            account_id=_str(entity.get("account_id")),
            program_id=_str(entity.get("program_id")),
            location_id=_str(entity.get("location_id")),
            points=_int(entity.get("points")),
            order_id=_opt_str(entity.get("order_id")),
            source=_str(entity.get("source"), "LOYALTY_API"),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "type": self.type,
                "account_id": self.account_id,
                "program_id": self.program_id,
                "location_id": self.location_id,
                "points": self.points,
                "order_id": self.order_id,
                "source": self.source,
            }
        )


@dataclass(frozen=True, slots=True)
class InventoryCountEntity:
    """The IN_STOCK quantity of one variation at one location.
    https://developer.squareup.com/reference/square/objects/InventoryCount --
    keyed by ``catalog_object_id`` + ``location_id``; ``quantity`` is a decimal string.
    """

    catalog_object_id: str
    location_id: str
    quantity: str = "0"
    state: str = "IN_STOCK"
    catalog_object_type: str = "ITEM_VARIATION"
    calculated_at: str = ""

    @property
    def id(self) -> str:
        return inventory_count_id(self.catalog_object_id, self.location_id)

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> InventoryCountEntity:
        return cls(
            catalog_object_id=_str(entity.get("catalog_object_id")),
            location_id=_str(entity.get("location_id")),
            quantity=_str(entity.get("quantity"), "0"),
            state=_str(entity.get("state"), "IN_STOCK"),
            catalog_object_type=_str(entity.get("catalog_object_type"), "ITEM_VARIATION"),
            calculated_at=_str(entity.get("calculated_at")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "catalog_object_id": self.catalog_object_id,
                "location_id": self.location_id,
                "quantity": self.quantity,
                "state": self.state,
                "catalog_object_type": self.catalog_object_type,
                "calculated_at": self.calculated_at or None,
            }
        )


def inventory_count_id(catalog_object_id: str, location_id: str) -> str:
    """The store id of a count: object and location, joined. Deterministic,
    so two units seeded alike hold the same ids and a change finds its row."""
    return f"{catalog_object_id}:{location_id}"


@dataclass(frozen=True, slots=True)
class AuthorizationCodeEntity:
    """An issued authorization code; ``id`` is the opaque code value. Expires 5 minutes after
    issue and is single-use (https://developer.squareup.com/docs/oauth-api/overview);
    ``used_at`` records that.
    """

    id: str
    client_id: str
    merchant_id: str
    expires_at: str
    scopes: tuple[str, ...] = ()
    redirect_uri: str | None = None
    code_challenge: str | None = None
    used_at: str | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> AuthorizationCodeEntity:
        return cls(
            id=_str(entity["id"]),
            client_id=_str(entity.get("client_id")),
            merchant_id=_str(entity.get("merchant_id")),
            expires_at=_str(entity.get("expires_at")),
            scopes=_str_tuple(entity.get("scopes")),
            redirect_uri=_opt_str(entity.get("redirect_uri")),
            code_challenge=_opt_str(entity.get("code_challenge")),
            used_at=_opt_str(entity.get("used_at")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "client_id": self.client_id,
                "merchant_id": self.merchant_id,
                "expires_at": self.expires_at,
                "scopes": list(self.scopes),
                "redirect_uri": self.redirect_uri,
                "code_challenge": self.code_challenge,
                "used_at": self.used_at,
            }
        )


@dataclass(frozen=True, slots=True)
class TokenEntity:
    """One issued access token and the refresh token that minted it.

    ``superseded_at`` marks an older token when a code-flow refresh reuses the same
    refresh-token string (https://developer.squareup.com/docs/oauth-api/overview) --
    silently, so the older access token stays valid until its own expiry.
    """

    id: str
    access_token: str
    refresh_token: str
    client_id: str
    merchant_id: str
    #: RFC 3339, seconds precision -- matching Square's ``expires_at``.
    expires_at: str
    scopes: tuple[str, ...] = ()
    #: What the seller approved at authorize time; distinct from ``scopes``, which narrows over
    #: time (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope). ``None``
    #: means "not recorded" (falls back to ``scopes``); an empty tuple means "recorded as
    #: nothing" and must NOT fall back (konyklabs/roadmap#28).
    authorized_scopes: tuple[str, ...] | None = None
    #: PKCE only; code-flow refresh tokens never expire, so the key is absent.
    refresh_token_expires_at: str | None = None
    short_lived: bool = False
    revoked_at: str | None = None
    superseded_at: str | None = None
    flow: Literal["code", "pkce"] = "code"

    @property
    def active(self) -> bool:
        """Neither revoked nor superseded; expiry is checked by the auth
        adapter against the unit clock."""
        return self.revoked_at is None and self.superseded_at is None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> TokenEntity:
        flow = _str(entity.get("flow"), "code")
        return cls(
            id=_str(entity["id"]),
            access_token=_str(entity.get("access_token")),
            refresh_token=_str(entity.get("refresh_token")),
            client_id=_str(entity.get("client_id")),
            merchant_id=_str(entity.get("merchant_id")),
            expires_at=_str(entity.get("expires_at")),
            scopes=_str_tuple(entity.get("scopes")),
            # .get distinguishes: an absent key is None ("not recorded"); a
            # present list, even empty, is the recorded approval (konyklabs/roadmap#28).
            authorized_scopes=(
                None if entity.get("authorized_scopes") is None else _str_tuple(entity.get("authorized_scopes"))
            ),
            refresh_token_expires_at=_opt_str(entity.get("refresh_token_expires_at")),
            short_lived=_bool(entity.get("short_lived")),
            revoked_at=_opt_str(entity.get("revoked_at")),
            superseded_at=_opt_str(entity.get("superseded_at")),
            flow="pkce" if flow == "pkce" else "code",
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "merchant_id": self.merchant_id,
                "expires_at": self.expires_at,
                "scopes": list(self.scopes),
                # NOT `or None`: an empty approval must survive as `[]`, or
                # the reader's fallback re-grants the token's scopes (konyklabs/roadmap#28).
                "authorized_scopes": None if self.authorized_scopes is None else list(self.authorized_scopes),
                "refresh_token_expires_at": self.refresh_token_expires_at,
                "short_lived": self.short_lived,
                "revoked_at": self.revoked_at,
                "superseded_at": self.superseded_at,
                "flow": self.flow,
            }
        )
