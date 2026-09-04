"""The order wire vocabulary: what a request may say, and what goes back out.

INVARIANT: an absent optional emits no key, through :func:`~vendorfake.core.util.json.compact`, the
one place ``None`` is erased. https://developer.squareup.com/reference/square/objects/Order
Line totals use :func:`~vendorfake.core.util.numbers.js_round` (halves round up) and
:func:`~vendorfake.core.util.numbers.js_parse_float` (a non-numeric ``quantity`` yields 0), matching
Square's wire behaviour.
UpdateOrder is sparse: "not mentioned" and "explicitly cleared" are different requests, told apart
with :func:`supplied` (``model_fields_set``), never ``is None``.
NOT VERIFIED -- Square states no rule for empty arrays: an optional array inside an entity
(``line_items``, ``tenders``) is omitted when empty; a collection an operation returns (``orders``,
``order_entries``) is always present, empty when there is nothing to return.
SHRINK (prototype): taxes, discounts, service charges, returns and refunds are not modelled; their
roll-up fields are emitted as zero money. ``extra="ignore"`` lets Square's unmodelled fields pass
through rather than fail the request.
Every request model is ``strict=True``, so e.g. ``{"version": "3"}`` is refused rather than coerced.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import js_parse_float, js_round
from vendorfake.square.entities import Fulfillment, Money, OrderEntity, OrderLineItem, Tender

__all__ = [
    "FULFILLMENT_TYPES",
    "BatchRetrieveOrdersRequest",
    "CreateOrderRequest",
    "DateTimeFilterRequest",
    "DeliveryDetailsRequest",
    "FulfillmentRecipientRequest",
    "FulfillmentRequest",
    "FulfillmentWire",
    "LineItemRequest",
    "LineItemWire",
    "MoneyRequest",
    "MoneyWire",
    "NetAmountsWire",
    "NewOrderRequest",
    "OrderEntryWire",
    "OrderPatch",
    "OrderSourceRequest",
    "OrderWire",
    "OrdersSortRequest",
    "PayOrderRequest",
    "PickupDetailsRequest",
    "SearchOrdersFilterRequest",
    "SearchOrdersQueryRequest",
    "SearchOrdersRequest",
    "ShipmentDetailsRequest",
    "StateFilterRequest",
    "TenderWire",
    "TimeRangeRequest",
    "UpdateOrderRequest",
    "amount_due",
    "line_item_total",
    "money",
    "order_total",
    "project_fulfillment",
    "project_line_item",
    "project_order",
    "project_order_entry",
    "supplied",
    "tendered_total",
    "tips_total",
]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Strict, so a money amount like ``2.5`` is refused rather than coerced -- minor units are whole numbers."""


class MoneyWire(BaseModel):
    """Square's ``Money``. Both fields are required; neither is ever absent."""

    model_config = _WIRE

    amount: int
    currency: str

    def wire(self) -> dict[str, Any]:
        return {"amount": self.amount, "currency": self.currency}


class LineItemWire(BaseModel):
    """One ``OrderLineItem``, with its own roll-ups, field order matching Square's own examples.
    https://developer.squareup.com/reference/square/objects/OrderLineItem https://developer.squareup.com/reference/square/orders-api/create-order"""

    model_config = _WIRE

    uid: str
    name: str | None = None
    quantity: str
    note: str | None = None
    catalog_object_id: str | None = None
    variation_name: str | None = None
    base_price_money: MoneyWire
    gross_sales_money: MoneyWire
    total_tax_money: MoneyWire
    total_service_charge_money: MoneyWire
    total_discount_money: MoneyWire
    total_money: MoneyWire
    variation_total_price_money: MoneyWire

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "uid": self.uid,
                "name": self.name,
                "quantity": self.quantity,
                "note": self.note,
                "catalog_object_id": self.catalog_object_id,
                "variation_name": self.variation_name,
                "base_price_money": self.base_price_money.wire(),
                "gross_sales_money": self.gross_sales_money.wire(),
                "total_tax_money": self.total_tax_money.wire(),
                "total_service_charge_money": self.total_service_charge_money.wire(),
                "total_discount_money": self.total_discount_money.wire(),
                "total_money": self.total_money.wire(),
                "variation_total_price_money": self.variation_total_price_money.wire(),
            }
        )


class TenderWire(BaseModel):
    """One ``Tender``. ``tip_money`` is present only when a tip was taken.
    https://developer.squareup.com/reference/square/objects/Tender"""

    model_config = _WIRE

    id: str
    location_id: str
    transaction_id: str
    created_at: str
    amount_money: MoneyWire
    tip_money: MoneyWire | None = None
    type: str
    payment_id: str

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "location_id": self.location_id,
                "transaction_id": self.transaction_id,
                "created_at": self.created_at,
                "amount_money": self.amount_money.wire(),
                "tip_money": None if self.tip_money is None else self.tip_money.wire(),
                "type": self.type,
                "payment_id": self.payment_id,
            }
        )


class FulfillmentWire(BaseModel):
    """One ``Fulfillment``. The details object is emitted only when present, and only the one named
    for the type is ever stored.
    https://developer.squareup.com/reference/square/objects/Fulfillment"""

    model_config = _WIRE

    uid: str
    type: str
    state: str
    line_item_application: str
    pickup_details: dict[str, Any] | None = None
    delivery_details: dict[str, Any] | None = None
    shipment_details: dict[str, Any] | None = None

    def wire(self) -> dict[str, Any]:
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


class NetAmountsWire(BaseModel):
    """``net_amounts``: "The net money amounts (sale money - return money)"."""

    model_config = _WIRE

    total_money: MoneyWire
    tax_money: MoneyWire
    discount_money: MoneyWire
    tip_money: MoneyWire
    service_charge_money: MoneyWire

    def wire(self) -> dict[str, Any]:
        return {
            "total_money": self.total_money.wire(),
            "tax_money": self.tax_money.wire(),
            "discount_money": self.discount_money.wire(),
            "tip_money": self.tip_money.wire(),
            "service_charge_money": self.service_charge_money.wire(),
        }


class OrderWire(BaseModel):
    """A whole ``Order``, ready to serialise."""

    model_config = _WIRE

    id: str
    location_id: str
    created_at: str
    updated_at: str
    state: str
    version: int
    total_money: MoneyWire
    total_tax_money: MoneyWire
    total_discount_money: MoneyWire
    total_tip_money: MoneyWire
    total_service_charge_money: MoneyWire
    net_amounts: NetAmountsWire
    net_amount_due_money: MoneyWire
    reference_id: str | None = None
    customer_id: str | None = None
    ticket_name: str | None = None
    source_name: str | None = None
    line_items: tuple[LineItemWire, ...] = ()
    fulfillments: tuple[FulfillmentWire, ...] = ()
    tenders: tuple[TenderWire, ...] = ()
    metadata: dict[str, str] | None = None
    closed_at: str | None = None

    def wire(self) -> dict[str, Any]:
        """The order as JSON, with every absent optional omitted."""
        return compact(
            {
                "id": self.id,
                "location_id": self.location_id,
                "reference_id": self.reference_id,
                "customer_id": self.customer_id,
                "ticket_name": self.ticket_name,
                "source": None if self.source_name is None else {"name": self.source_name},
                "line_items": [item.wire() for item in self.line_items] if self.line_items else None,
                "fulfillments": [f.wire() for f in self.fulfillments] if self.fulfillments else None,
                "metadata": self.metadata,
                "tenders": [tender.wire() for tender in self.tenders] if self.tenders else None,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "closed_at": self.closed_at,
                "state": self.state,
                "version": self.version,
                "total_money": self.total_money.wire(),
                "total_tax_money": self.total_tax_money.wire(),
                "total_discount_money": self.total_discount_money.wire(),
                "total_tip_money": self.total_tip_money.wire(),
                "total_service_charge_money": self.total_service_charge_money.wire(),
                "net_amounts": self.net_amounts.wire(),
                "net_amount_due_money": self.net_amount_due_money.wire(),
            }
        )


class OrderEntryWire(BaseModel):
    """``OrderEntry``, returned by SearchOrders when ``return_entries`` is true.
    https://developer.squareup.com/reference/square/orders-api/search-orders"""

    model_config = _WIRE

    order_id: str
    version: int
    location_id: str

    def wire(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "version": self.version, "location_id": self.location_id}


# ---------------------------------------------------------------------------
# Arithmetic -- see the module docstring for the rounding/parsing rule.
# ---------------------------------------------------------------------------


def money(amount: int, currency: str) -> MoneyWire:
    """A money object in minor units."""
    return MoneyWire(amount=amount, currency=currency)


def line_item_total(item: OrderLineItem) -> int:
    """Line total: ``base_price_money.amount`` times parsed quantity, halves rounded up. JUDGMENT,
    NOT VERIFIED -- a negative quantity is accepted and produces a negative total; a non-numeric or
    non-finite quantity is treated as 0 rather than raising."""
    quantity = js_parse_float(item.quantity)
    if quantity is None or not math.isfinite(quantity):
        return 0
    return js_round(item.base_price_money.amount * quantity)


def order_total(order: OrderEntity) -> int:
    """The sum of the line totals, in minor units -- before any tip a buyer adds at payment."""
    return sum(line_item_total(item) for item in order.line_items)


def tendered_total(order: OrderEntity) -> int:
    """Everything the tenders carry, tips included, in minor units."""
    return sum(tender.amount_money.amount for tender in order.tenders)


def tips_total(order: OrderEntity) -> int:
    """The tips the tenders carry, in minor units: ``total_tip_money``."""
    return sum(0 if tender.tip_money is None else tender.tip_money.amount for tender in order.tenders)


def amount_due(order: OrderEntity) -> int:
    """What is still owed: order total minus tenders' non-tip amount, clamped at zero; a tip never
    reduces it."""
    applied = sum(tender.applied for tender in order.tenders)
    return max(0, order_total(order) - applied)


# ---------------------------------------------------------------------------
# Projections.
# ---------------------------------------------------------------------------


def _money_wire(value: Money) -> MoneyWire:
    return MoneyWire(amount=value.amount, currency=value.currency)


def project_line_item(item: OrderLineItem, currency: str) -> LineItemWire:
    """One stored line item as Square's ``OrderLineItem``."""
    total = money(line_item_total(item), currency)
    zero = money(0, currency)
    return LineItemWire(
        uid=item.uid,
        catalog_object_id=item.catalog_object_id,
        variation_name=item.variation_name,
        name=item.name,
        quantity=item.quantity,
        note=item.note,
        base_price_money=_money_wire(item.base_price_money),
        variation_total_price_money=total,
        gross_sales_money=total,
        total_tax_money=zero,
        total_discount_money=zero,
        total_money=total,
        total_service_charge_money=zero,
    )


def project_fulfillment(fulfillment: Fulfillment) -> FulfillmentWire:
    """One stored fulfillment as Square's ``Fulfillment``."""
    return FulfillmentWire(
        uid=fulfillment.uid,
        type=fulfillment.type,
        state=fulfillment.state,
        line_item_application=fulfillment.line_item_application,
        pickup_details=fulfillment.pickup_details,
        delivery_details=fulfillment.delivery_details,
        shipment_details=fulfillment.shipment_details,
    )


def _project_tender(tender: Tender) -> TenderWire:
    return TenderWire(
        id=tender.id,
        location_id=tender.location_id,
        transaction_id=tender.transaction_id,
        created_at=tender.created_at,
        amount_money=_money_wire(tender.amount_money),
        tip_money=None if tender.tip_money is None else _money_wire(tender.tip_money),
        type=tender.type,
        payment_id=tender.payment_id,
    )


def project_order(order: OrderEntity) -> dict[str, Any]:
    """A stored order as Square's ``Order`` JSON, absent optionals omitted."""
    currency = order.currency
    tips = tips_total(order)
    # JUDGMENT / NOT VERIFIED -- total_money includes tenders' tips, so tenders reconcile to it on a
    # paid order; Square does not say whether a tip rolls into total_money or stays in total_tip_money.
    total = order_total(order) + tips
    zero = money(0, currency)
    total_money = money(total, currency)
    tip_money = money(tips, currency)
    return OrderWire(
        id=order.id,
        location_id=order.location_id,
        reference_id=order.reference_id,
        customer_id=order.customer_id,
        ticket_name=order.ticket_name,
        source_name=order.source_name,
        line_items=tuple(project_line_item(item, currency) for item in order.line_items),
        fulfillments=tuple(project_fulfillment(f) for f in order.fulfillments),
        metadata=order.metadata,
        tenders=tuple(_project_tender(tender) for tender in order.tenders),
        created_at=order.created_at,
        updated_at=order.updated_at,
        closed_at=order.closed_at,
        state=order.state,
        version=order.version,
        total_money=total_money,
        total_tax_money=zero,
        total_discount_money=zero,
        total_tip_money=tip_money,
        total_service_charge_money=zero,
        net_amounts=NetAmountsWire(
            total_money=total_money,
            tax_money=zero,
            discount_money=zero,
            tip_money=tip_money,
            service_charge_money=zero,
        ),
        # JUDGMENT / NOT VERIFIED -- always emitted though Square's examples omit this read-only
        # field; clamped at zero via `amount_due` even on a negative order total.
        net_amount_due_money=money(amount_due(order), currency),
    ).wire()


def project_order_entry(order: OrderEntity) -> dict[str, Any]:
    """A stored order as an ``OrderEntry``."""
    return OrderEntryWire(order_id=order.id, version=order.version, location_id=order.location_id).wire()


# ---------------------------------------------------------------------------
# Requests -- see the module docstring for the absent/null rule and the
# strict=True rationale.
# ---------------------------------------------------------------------------

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)


def supplied(model: BaseModel, field: str) -> bool:
    """Whether ``field`` was present in the request at all -- ``model.field is None`` can't answer
    this, since an absent field and an explicit ``null`` both validate to ``None``."""
    return field in model.model_fields_set


class MoneyRequest(BaseModel):
    """``Money`` as a caller may send it. ``currency`` is optional here (Square requires it) since
    the order's location supplies a default.
    https://developer.squareup.com/reference/square/objects/Money"""

    model_config = _REQUEST

    amount: int
    currency: str | None = None

    def entity(self, fallback_currency: str) -> Money:
        return Money(amount=self.amount, currency=self.currency or fallback_currency)


class OrderSourceRequest(BaseModel):
    """``order.source``. Only ``name`` is modelled; Square's object has no other writable field.
    https://developer.squareup.com/reference/square/objects/OrderSource"""

    model_config = _REQUEST

    name: str | None = None


class LineItemRequest(BaseModel):
    """One entry of ``order.line_items``, on create or a sparse update -- every field is optional
    here since both modes share this model; the surface enforces what each requires. The documented
    *minimum* length on ``quantity`` is deliberately not enforced: an empty ``quantity`` means
    "omitted" on create and "clear it" on update, each with its own error."""

    model_config = _REQUEST

    uid: str | None = Field(default=None, max_length=60)
    name: str | None = Field(default=None, max_length=512)
    quantity: str | None = Field(default=None, max_length=12)
    note: str | None = Field(default=None, max_length=2000)
    catalog_object_id: str | None = None
    variation_name: str | None = None
    base_price_money: MoneyRequest | None = None


FULFILLMENT_TYPES: tuple[str, ...] = ("PICKUP", "SHIPMENT", "DELIVERY")
"""The three ``FulfillmentType`` values.
https://developer.squareup.com/reference/square/enums/FulfillmentType"""

_DETAILS = ConfigDict(extra="ignore", frozen=True, strict=True)
"""The three details models list every field their page documents; anything else is dropped."""


class FulfillmentRecipientRequest(BaseModel):
    """``recipient`` on any of the three details objects.
    https://developer.squareup.com/reference/square/objects/FulfillmentRecipient"""

    model_config = _DETAILS

    customer_id: str | None = None
    display_name: str | None = None
    email_address: str | None = None
    phone_number: str | None = None
    address: dict[str, Any] | None = None


class PickupDetailsRequest(BaseModel):
    """``pickup_details``, every documented field.
    https://developer.squareup.com/reference/square/objects/FulfillmentPickupDetails
    JUDGMENT / NOT VERIFIED -- the ``*_at`` stamps
    are accepted from the caller even though Square marks several read-only; this unit stores what
    was sent and stamps only what was not."""

    model_config = _DETAILS

    recipient: FulfillmentRecipientRequest | None = None
    expires_at: str | None = None
    auto_complete_duration: str | None = None
    schedule_type: str | None = None
    pickup_at: str | None = None
    pickup_window_duration: str | None = None
    prep_time_duration: str | None = None
    note: str | None = None
    placed_at: str | None = None
    accepted_at: str | None = None
    rejected_at: str | None = None
    ready_at: str | None = None
    expired_at: str | None = None
    picked_up_at: str | None = None
    canceled_at: str | None = None
    cancel_reason: str | None = None
    is_curbside_pickup: bool | None = None
    curbside_pickup_details: dict[str, Any] | None = None


class DeliveryDetailsRequest(BaseModel):
    """``delivery_details``, every documented field.
    https://developer.squareup.com/reference/square/objects/FulfillmentDeliveryDetails
    Same JUDGMENT on the ``*_at`` stamps as
    :class:`PickupDetailsRequest`."""

    model_config = _DETAILS

    recipient: FulfillmentRecipientRequest | None = None
    schedule_type: str | None = None
    placed_at: str | None = None
    deliver_at: str | None = None
    prep_time_duration: str | None = None
    delivery_window_duration: str | None = None
    note: str | None = None
    completed_at: str | None = None
    in_progress_at: str | None = None
    rejected_at: str | None = None
    ready_at: str | None = None
    delivered_at: str | None = None
    canceled_at: str | None = None
    cancel_reason: str | None = None
    courier_pickup_at: str | None = None
    courier_pickup_window_duration: str | None = None
    is_no_contact_delivery: bool | None = None
    dropoff_notes: str | None = None
    courier_provider_name: str | None = None
    courier_support_phone_number: str | None = None
    square_delivery_id: str | None = None
    external_delivery_id: str | None = None
    managed_delivery: bool | None = None


class ShipmentDetailsRequest(BaseModel):
    """``shipment_details``, every documented field.
    https://developer.squareup.com/reference/square/objects/FulfillmentShipmentDetails"""

    model_config = _DETAILS

    recipient: FulfillmentRecipientRequest | None = None
    carrier: str | None = None
    shipping_note: str | None = None
    shipping_type: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    placed_at: str | None = None
    in_progress_at: str | None = None
    packaged_at: str | None = None
    expected_shipped_at: str | None = None
    shipped_at: str | None = None
    canceled_at: str | None = None
    cancel_reason: str | None = None
    failed_at: str | None = None
    failure_reason: str | None = None


class FulfillmentRequest(BaseModel):
    """One entry of ``order.fulfillments``, on create or a sparse update -- every field optional for
    the reason :class:`LineItemRequest` gives. ``state`` on create defaults to ``PROPOSED``.
    https://developer.squareup.com/reference/square/objects/Fulfillment"""

    model_config = _REQUEST

    uid: str | None = Field(default=None, max_length=60)
    type: str | None = None
    state: str | None = None
    pickup_details: PickupDetailsRequest | None = None
    delivery_details: DeliveryDetailsRequest | None = None
    shipment_details: ShipmentDetailsRequest | None = None


class NewOrderRequest(BaseModel):
    """``order`` on CreateOrder.
    https://developer.squareup.com/reference/square/orders-api/create-order"""

    model_config = _REQUEST

    location_id: str = Field(min_length=1)
    reference_id: str | None = None
    customer_id: str | None = None
    ticket_name: str | None = None
    #: OPEN (the default) or DRAFT; the surface refuses the two terminal values.
    state: str | None = None
    source: OrderSourceRequest | None = None
    line_items: list[LineItemRequest] | None = None
    fulfillments: list[FulfillmentRequest] | None = None
    metadata: dict[str, str] | None = None


class CreateOrderRequest(BaseModel):
    """``POST /v2/orders``. ``idempotency_key`` is read by the kernel from the route's
    :class:`~vendorfake.core.kernel.types.IdempotencySpec`, declared here only for documentation."""

    model_config = _REQUEST

    order: NewOrderRequest
    idempotency_key: str | None = None


class OrderPatch(BaseModel):
    """``order`` on UpdateOrder: the sparse half. ``version`` is optional in the type but required
    by the surface, so an omitted ``version`` reports Square's own documented message.
    https://developer.squareup.com/docs/orders-api/manage-orders/update-orders"""

    model_config = _REQUEST

    version: int | None = None
    state: str | None = None
    reference_id: str | None = None
    customer_id: str | None = None
    ticket_name: str | None = None
    metadata: dict[str, str] | None = None
    line_items: list[LineItemRequest] | None = None
    fulfillments: list[FulfillmentRequest] | None = None


class UpdateOrderRequest(BaseModel):
    """``PUT /v2/orders/{order_id}``. ``fields_to_clear`` is top-level, beside ``order``, matching
    where Square documents it.
    https://developer.squareup.com/reference/square/orders-api/update-order"""

    model_config = _REQUEST

    order: OrderPatch
    fields_to_clear: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class PayOrderRequest(BaseModel):
    """``POST /v2/orders/{order_id}/pay``. An absent ``order_version`` pays the latest version
    rather than version 0.
    https://developer.squareup.com/reference/square/orders-api/pay-order"""

    model_config = _REQUEST

    idempotency_key: str | None = None
    order_version: int | None = None
    payment_ids: list[str] | None = None


class TimeRangeRequest(BaseModel):
    """A ``TimeRange``: start inclusive, end exclusive.
    https://developer.squareup.com/reference/square/objects/TimeRange"""

    model_config = _REQUEST

    start_at: str | None = None
    end_at: str | None = None


class DateTimeFilterRequest(BaseModel):
    """``query.filter.date_time_filter``; whichever of the three fields is used must match the
    query's ``sort_field``, read with :func:`supplied`.
    https://developer.squareup.com/reference/square/objects/SearchOrdersDateTimeFilter"""

    model_config = _REQUEST

    created_at: TimeRangeRequest | None = None
    updated_at: TimeRangeRequest | None = None
    closed_at: TimeRangeRequest | None = None


class StateFilterRequest(BaseModel):
    """``query.filter.state_filter``.
    https://developer.squareup.com/reference/square/objects/SearchOrdersStateFilter"""

    model_config = _REQUEST

    states: list[str] = Field(default_factory=list)


class SearchOrdersFilterRequest(BaseModel):
    """``query.filter``."""

    model_config = _REQUEST

    state_filter: StateFilterRequest | None = None
    date_time_filter: DateTimeFilterRequest | None = None


class OrdersSortRequest(BaseModel):
    """``query.sort``. Defaults are Square's: CREATED_AT, DESC.
    https://developer.squareup.com/reference/square/objects/SearchOrdersSort"""

    model_config = _REQUEST

    sort_field: str | None = None
    sort_order: str | None = None


class SearchOrdersQueryRequest(BaseModel):
    """``query``. ``filter`` shadows no attribute of ``BaseModel``; it is
    Square's own field name."""

    model_config = _REQUEST

    filter: SearchOrdersFilterRequest | None = None
    sort: OrdersSortRequest | None = None


class SearchOrdersRequest(BaseModel):
    """``POST /v2/orders/search``. ``limit`` defaults to 500 and maxes at 1000; ``location_ids``
    maxes at 10 -- both enforced in the surface.
    https://developer.squareup.com/reference/square/orders-api/search-orders"""

    model_config = _REQUEST

    #: Required, but checked in the surface so an omitted and an empty list produce the same error.
    #: https://developer.squareup.com/docs/orders-api/manage-orders/search-orders
    location_ids: list[str] | None = None
    query: SearchOrdersQueryRequest | None = None
    cursor: str | None = None
    limit: int | None = None
    #: "If set to true, returns the OrderEntry objects instead of Order objects."
    return_entries: bool = False


class BatchRetrieveOrdersRequest(BaseModel):
    """``POST /v2/orders/batch-retrieve``.
    https://developer.squareup.com/reference/square/orders-api/batch-retrieve-orders"""

    model_config = _REQUEST

    order_ids: list[str]
    #: Deprecated on Square's object; omitted, it scopes to the current authorization's merchant.
    location_id: str | None = None
