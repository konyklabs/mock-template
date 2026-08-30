"""The shapes this vendor stores, and the collections it stores them in.

FOR: giving the surfaces one typed reading of every stored entity, so that the
name of a stored field is written down once instead of being spelled as a
dictionary key in each handler that touches it.

INVARIANT: **absence is absence.** A field a merchant never set is *missing*
from the entity dict; it is never present with the value ``None``. JavaScript
gets this free -- ``JSON.stringify`` and ``structuredClone`` both drop
``undefined`` -- and Python does not, so it is a rule instead: every
:meth:`to_entity` drops its unset optionals through the core's ``compact()``,
and a field is cleared with ``pop`` and never with ``= None``. Three things
depend on it: the entity digest (which hashes stored fields), the journal's
``changed`` list (which compares present against absent), and the wire
projection (where Square omits the key rather than sending a null).

The stored model is this unit's own, and the projections in :mod:`.model`
translate it to Square's wire JSON. Keeping the two apart means a field Square
renames is a one-line change in a projector rather than a rename across the
state engine.

Two departures from the reference worth naming:

**Reads are typed views, writes are dicts.** The reference declares
``interface OrderEntity extends Entity`` and relies on TypeScript to check
every dictionary access. The store here holds ``dict[str, Any]`` -- entities are
produced internally, deep-copied on every read and write, and never parsed from
an external document -- so structure comes from a frozen dataclass with a
``from_entity`` reader, exactly as the core's own ``Subscription`` does it.
Handlers that mutate keep mutating the draft dict the store hands them; handlers
that read get a typed object.

**Collection names are snake_case.** The reference spells two of its six in
camelCase (``catalogObjects``, ``authorizationCodes``) because its whole entity
model is camelCase. This build snake_cases entity keys everywhere -- the core
itself ships ``notification_url`` and ``event_types``, and the control plane
publishes snake_case throughout -- so a camelCase collection name would be the
only camelCase identifier at ``GET /__unit/state``. The vocabulary is
unchanged; four of the six names are byte-identical. Recorded as
``provenance: judgment``.
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
    "LocationEntity",
    "MerchantEntity",
    "Money",
    "OrderEntity",
    "OrderLineItem",
    "PaymentEntity",
    "SquareCollections",
    "Tender",
    "TokenEntity",
]


@dataclass(frozen=True, slots=True)
class SquareCollections:
    """The store collections this vendor uses, named once."""

    merchants: str = "merchants"
    locations: str = "locations"
    catalog: str = "catalog_objects"
    orders: str = "orders"
    payments: str = "payments"
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
            self.codes,
            self.tokens,
        )


COL = SquareCollections()
"""The one place a collection name is spelled."""


# ---------------------------------------------------------------------------
# Readers. Tolerant on type, strict on presence: a stored entity is produced by
# this package, so a wrong type is a defect here rather than bad input, and
# coercing it quietly beats raising from inside a projection.
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

    https://developer.squareup.com/reference/square/objects/Money
    """

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
    """An ITEM or an ITEM_VARIATION. One collection holds both, as Square does.

    https://developer.squareup.com/reference/square/objects/CatalogObject
    """

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
    """One line of an order. Nested inside the order entity, not a collection.

    ``quantity`` is a **string**, as Square sends it
    (https://developer.squareup.com/reference/square/objects/OrderLineItem), so
    a consumer may legitimately send ``"2"`` or illegitimately send anything
    else. The projection is where that is handled.
    """

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
    """One payment against an order.

    https://developer.squareup.com/reference/square/objects/Tender

    JUDGMENT -- ``type`` defaults to ``CARD``. It is a real ``TenderType``
    value (https://developer.squareup.com/reference/square/enums/TenderType)
    and it is what the PayOrder example response shows
    (https://developer.squareup.com/reference/square/orders-api/pay-order), but
    Square derives it from the *payment*, and this unit has no Payments API to
    derive it from -- see the SHRINK in
    :mod:`vendorfake.square.surface.orders`. So the value is this unit's
    choice, not a documented consequence of anything the caller sent, and a
    consumer must not test that a particular payment produced a particular
    tender type here. A scenario can state a different one on a seeded tender.
    """

    id: str
    location_id: str
    transaction_id: str
    created_at: str
    amount_money: Money
    type: str = "CARD"
    payment_id: str = ""

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
        )

    def to_entity(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "location_id": self.location_id,
            "transaction_id": self.transaction_id,
            "created_at": self.created_at,
            "amount_money": self.amount_money.to_entity(),
            "type": self.type,
            "payment_id": self.payment_id,
        }


@dataclass(frozen=True, slots=True)
class Fulfillment:
    """One fulfillment of an order: how the buyer receives it.

    https://developer.squareup.com/reference/square/objects/Fulfillment --
    ``uid``, ``type`` (``PICKUP``, ``SHIPMENT``, ``DELIVERY``), ``state``
    (https://developer.squareup.com/reference/square/enums/FulfillmentState),
    ``line_item_application`` (``ALL`` when the fulfillment covers the whole
    order) and one details object named for the type.

    The details are stored as the mapping the request models produced --
    documented field names only, absent keys absent -- because each of the
    three details objects has twenty-odd optional fields and a typed view of
    every one would be a page of readers nothing else consults. The request
    models in :mod:`vendorfake.square.model.order` are where the field lists
    live.
    """

    uid: str
    type: str
    state: str = "PROPOSED"
    line_item_application: str = "ALL"
    pickup_details: dict[str, Any] | None = None
    delivery_details: dict[str, Any] | None = None
    shipment_details: dict[str, Any] | None = None

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
            }
        )


def _details(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return None


@dataclass(frozen=True, slots=True)
class OrderEntity:
    """An order, as stored.

    ``version``, ``created_at`` and ``updated_at`` are the store's, not this
    vendor's: the store bumps the version on every committed write and Square
    documents ``version`` as read-only, "incremented each time an update is
    committed to the order"
    (https://developer.squareup.com/reference/square/objects/Order).
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
        """The dict the store holds.

        ``created_at`` and ``updated_at`` are omitted when empty so that
        ``Collection.insert`` fills them from the clock; a seeded order that
        states them keeps its own.
        """
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
    """A payment, as stored.

    https://developer.squareup.com/reference/square/objects/Payment. Only the
    ``EXTERNAL`` source is modelled -- see the SHRINK in
    :mod:`vendorfake.square.surface.payments` -- so ``external_details`` is
    the one source-specific block and ``source_type`` is always ``EXTERNAL``
    on anything this unit mints. ``version``, ``created_at`` and
    ``updated_at`` are the store's, as on an order; ``version_token`` on the
    wire is derived from the store version.
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
class AuthorizationCodeEntity:
    """An issued authorization code. ``id`` is the opaque code value itself.

    "The authorization code expires 5 minutes after the Square authorization
    page generates the code", and it is single-use
    (https://developer.squareup.com/docs/oauth-api/overview), which is what
    ``used_at`` records.
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

    ``superseded_at`` has no counterpart in the reference and exists because of
    a documented behaviour the reference got wrong: "A refresh token obtained
    using the code flow can be used to get multiple active access tokens"
    (https://developer.squareup.com/docs/oauth-api/overview), so a code-flow
    refresh must **not** revoke the previous access token. Code flow also
    returns the *same* refresh-token string
    (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope),
    which leaves two live records sharing one refresh token and a lookup that
    would find the stale one. Marking the older record superseded -- a silent
    update, so no version bump, no journal entry and no webhook -- keeps the
    refresh lookup single-valued while the older *access* token stays valid
    until its own expiry, which is what Square documents.
    """

    id: str
    access_token: str
    refresh_token: str
    client_id: str
    merchant_id: str
    #: RFC 3339, seconds precision -- matching Square's ``expires_at``.
    expires_at: str
    scopes: tuple[str, ...] = ()
    #: What the SELLER APPROVED at authorize time, carried forward unchanged by
    #: every refresh. Distinct from ``scopes``, which is what this particular
    #: token carries after any narrowing.
    #:
    #: Square narrows "from the ones granted when the seller approved"
    #: (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope),
    #: so a refresh intersects against the approval and not against whatever the
    #: last refresh happened to ask for. Intersecting against the current
    #: token's scopes makes every narrowing permanent: take a narrow token for
    #: one subtask and the grant can never produce a full one again. Empty means
    #: "not recorded", and callers fall back to ``scopes`` -- right for a seeded
    #: token that never came from a grant.
    authorized_scopes: tuple[str, ...] = ()
    #: PKCE only: "Refresh tokens obtained using the PKCE flow ... expire after
    #: 90 days." Code-flow refresh tokens do not expire, so the key is absent.
    refresh_token_expires_at: str | None = None
    short_lived: bool = False
    revoked_at: str | None = None
    superseded_at: str | None = None
    flow: Literal["code", "pkce"] = "code"

    @property
    def active(self) -> bool:
        """Neither revoked nor superseded. Expiry is a clock question, so it is
        not answered here -- the auth adapter compares against the unit clock."""
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
            authorized_scopes=_str_tuple(entity.get("authorized_scopes")),
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
                "authorized_scopes": list(self.authorized_scopes) or None,
                "refresh_token_expires_at": self.refresh_token_expires_at,
                "short_lived": self.short_lived,
                "revoked_at": self.revoked_at,
                "superseded_at": self.superseded_at,
                "flow": self.flow,
            }
        )
