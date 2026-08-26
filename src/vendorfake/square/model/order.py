"""The order wire vocabulary: what a request may say, and what goes back out.

FOR: emitting exactly the document Square's own examples show, including the
read-only money roll-ups Square computes rather than accepts, so that a
consumer deserialising with Square's SDK gets the fields it expects -- and,
below the projections, stating what each Orders request accepts as models
rather than as hand-written key lookups.

INVARIANT: **an absent optional emits no key.** Every projection is assembled
through the core's ``compact()``, which is the one place ``None`` is erased. The
reference gets this from ``JSON.stringify`` dropping ``undefined``; Python has
no such value, so without ``compact()`` every order with no ``reference_id``
would carry ``"reference_id": null`` -- and ``customer_id``, ``ticket_name``,
``source``, ``line_items``, ``metadata``, ``tenders`` and ``closed_at`` with it.
A consumer writing ``if "closed_at" in order`` would then take the wrong branch
on every open order.

Field names and the read-only roll-ups follow
https://developer.squareup.com/reference/square/objects/Order and the
CreateOrder response example on
https://developer.squareup.com/reference/square/orders-api/create-order.
``version``, ``state``, ``created_at``, ``updated_at``, ``closed_at`` ("The
timestamp for when the order reached a terminal state, in RFC 3339 format") and
``net_amounts`` ("The net money amounts (sale money - return money)") are all
documented read-only, which is why they are computed here rather than accepted
from a request.

SHRINK (prototype): taxes, discounts, service charges, fulfillments, returns
and refunds are not modelled. The corresponding roll-up fields are emitted as
zero money so a consumer deserialising the full ``Order`` shape still works,
and ``net_amounts`` is therefore always equal to ``total_money``.

Money arithmetic
----------------
``Math.round(base_price_minor * Number.parseFloat(quantity))`` is the
reference's line total, and both halves of it are JavaScript-specific:

* ``Math.round`` rounds halves **upward** -- ``Math.round(2.5) === 3`` and
  ``Math.round(-0.5)`` is ``-0``. Python's ``round`` is banker's rounding:
  ``round(2.5) == 2``, ``round(3.5) == 4``, ``round(-0.5) == 0``. The two
  disagree on every halfway case, which a line of ``quantity: "0.5"`` at an odd
  price reaches immediately. :func:`~vendorfake.core.util.numbers.js_round` is
  ``floor(x + 0.5)`` and is what is used here.
* ``Number.parseFloat`` consumes the longest numeric **prefix** and yields
  ``NaN`` when there is none, and the reference maps a non-finite quantity to a
  line total of 0. Python's ``float("2 pieces")`` raises, which would turn a
  documented 200 into an uncaught 500 on traffic a consumer is entitled to
  send, because Square's ``quantity`` is a *string* field.
  :func:`~vendorfake.core.util.numbers.js_parse_float` scans the prefix and
  returns ``None``, which this module maps to 0.

Both are pinned by tests in ``tests/unit/square/test_model_order.py``.

Absent, null and empty
----------------------
The request models exist for one reason a dict lookup cannot serve: UpdateOrder
is *sparse*, so "the caller did not mention this field" and "the caller asked
for this field to be cleared" are different requests that must produce different
orders. TypeScript separates them for free -- ``patch.reference_id !==
undefined`` is false for an absent key and true for an explicit ``null``. Python
has one empty value, so the distinction is carried by
:func:`supplied`, i.e. by Pydantic's ``model_fields_set``, and never by testing
the value against ``None``. Getting that wrong in either direction is a real
data loss: read an order, echo it back with one field changed, and every
optional the caller did not mention is either wiped or frozen.

Strictness
----------
Every request model here is ``strict=True``. The reference guards three
decisions with ``typeof`` -- ``typeof version !== 'number'`` on UpdateOrder,
``typeof body.limit === 'number'`` on SearchOrders, ``typeof body.order_version
=== 'number'`` on PayOrder -- plus ``body.return_entries === true``, and
Pydantic's default lax coercion would delete all four: ``{"version": "3"}``
would be accepted as 3, ``{"return_entries": "yes"}`` as true. Strict validation
is how those gates survive the port.

Three tightenings come with it, and they are tightenings rather than fidelity
losses -- every one turns a value the reference *silently ignored* into a 400
that names the field:

* ``{"limit": "5"}`` is ``invalid_value``; the reference read it as "no limit".
* ``{"return_entries": "true"}`` is ``invalid_value``; the reference read it as
  false, so a consumer asking for order entries silently received whole orders.
* ``{"order": {"version": 3.5}}`` is ``invalid_value`` on ``order.version``;
  the reference accepted 3.5 as a number and then failed it as a *version
  conflict*, which tells the caller to re-read the order rather than to fix
  their type.

``extra="ignore"`` rather than ``extra="forbid"``: Square's Order carries
taxes, discounts, fulfillments and much else that this unit does not model
(see the SHRINK above), and refusing a request because it mentioned one of them
would fail on the shrink rather than on the thing under test.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import js_parse_float, js_round
from vendorfake.square.entities import Money, OrderEntity, OrderLineItem, Tender

__all__ = [
    "BatchRetrieveOrdersRequest",
    "CreateOrderRequest",
    "DateTimeFilterRequest",
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
    "SearchOrdersFilterRequest",
    "SearchOrdersQueryRequest",
    "SearchOrdersRequest",
    "StateFilterRequest",
    "TenderWire",
    "TimeRangeRequest",
    "UpdateOrderRequest",
    "line_item_total",
    "money",
    "order_total",
    "project_line_item",
    "project_order",
    "project_order_entry",
    "supplied",
    "tendered_total",
]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Strict, so that a money amount which arrived as ``2.5`` is refused here
rather than being coerced to 2 somewhere on the way to the wire. Minor units
are whole numbers by definition."""


class MoneyWire(BaseModel):
    """Square's ``Money``. Both fields are required; neither is ever absent."""

    model_config = _WIRE

    amount: int
    currency: str

    def wire(self) -> dict[str, Any]:
        return {"amount": self.amount, "currency": self.currency}


class LineItemWire(BaseModel):
    """One ``OrderLineItem``, with its own roll-ups.

    Field order is the reference's, which is the order Square's CreateOrder
    example prints, because a response body is read by humans as often as by
    parsers.
    """

    model_config = _WIRE

    uid: str
    quantity: str
    base_price_money: MoneyWire
    variation_total_price_money: MoneyWire
    gross_sales_money: MoneyWire
    total_tax_money: MoneyWire
    total_discount_money: MoneyWire
    total_money: MoneyWire
    total_service_charge_money: MoneyWire
    catalog_object_id: str | None = None
    variation_name: str | None = None
    name: str | None = None
    note: str | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "uid": self.uid,
                "catalog_object_id": self.catalog_object_id,
                "variation_name": self.variation_name,
                "name": self.name,
                "quantity": self.quantity,
                "note": self.note,
                "base_price_money": self.base_price_money.wire(),
                "variation_total_price_money": self.variation_total_price_money.wire(),
                "gross_sales_money": self.gross_sales_money.wire(),
                "total_tax_money": self.total_tax_money.wire(),
                "total_discount_money": self.total_discount_money.wire(),
                "total_money": self.total_money.wire(),
                "total_service_charge_money": self.total_service_charge_money.wire(),
            }
        )


class TenderWire(BaseModel):
    """One ``Tender``. No optional fields, so no key is ever omitted."""

    model_config = _WIRE

    id: str
    location_id: str
    transaction_id: str
    created_at: str
    amount_money: MoneyWire
    type: str
    payment_id: str

    def wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "location_id": self.location_id,
            "transaction_id": self.transaction_id,
            "created_at": self.created_at,
            "amount_money": self.amount_money.wire(),
            "type": self.type,
            "payment_id": self.payment_id,
        }


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
    tenders: tuple[TenderWire, ...] = ()
    metadata: dict[str, str] | None = None
    closed_at: str | None = None

    def wire(self) -> dict[str, Any]:
        """The order as JSON, with every absent optional omitted.

        ``line_items`` and ``tenders`` are omitted when empty rather than sent
        as ``[]``: the reference passes ``undefined`` for an empty list and
        Square's own examples carry no ``line_items`` key on an order that has
        none. ``source`` is a nested object built from a single stored name.
        """
        return compact(
            {
                "id": self.id,
                "location_id": self.location_id,
                "reference_id": self.reference_id,
                "customer_id": self.customer_id,
                "ticket_name": self.ticket_name,
                "source": None if self.source_name is None else {"name": self.source_name},
                "line_items": [item.wire() for item in self.line_items] if self.line_items else None,
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

    https://developer.squareup.com/reference/square/orders-api/search-orders
    """

    model_config = _WIRE

    order_id: str
    version: int
    location_id: str

    def wire(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "version": self.version, "location_id": self.location_id}


# ---------------------------------------------------------------------------
# Arithmetic. See the module docstring for why neither half is a Python builtin.
# ---------------------------------------------------------------------------


def money(amount: int, currency: str) -> MoneyWire:
    """A money object in minor units."""
    return MoneyWire(amount=amount, currency=currency)


def line_item_total(item: OrderLineItem) -> int:
    """``Math.round(base * parseFloat(quantity))``, with both halves ported.

    A quantity with no numeric prefix -- ``""``, ``"pieces"`` -- and a
    non-finite one -- ``"Infinity"`` -- both give 0, which is what the
    reference's ``!Number.isFinite(qty)`` guard produces. Returning 0 rather
    than raising is deliberate: ``quantity`` is a string on Square's wire, so
    junk in it is expected traffic and must not become a 500.
    """
    quantity = js_parse_float(item.quantity)
    if quantity is None or not math.isfinite(quantity):
        return 0
    return js_round(item.base_price_money.amount * quantity)


def order_total(order: OrderEntity) -> int:
    """The sum of the line totals, in minor units."""
    return sum(line_item_total(item) for item in order.line_items)


def tendered_total(order: OrderEntity) -> int:
    """How much has been paid, in minor units."""
    return sum(tender.amount_money.amount for tender in order.tenders)


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


def _project_tender(tender: Tender) -> TenderWire:
    return TenderWire(
        id=tender.id,
        location_id=tender.location_id,
        transaction_id=tender.transaction_id,
        created_at=tender.created_at,
        amount_money=_money_wire(tender.amount_money),
        type=tender.type,
        payment_id=tender.payment_id,
    )


def project_order(order: OrderEntity) -> dict[str, Any]:
    """A stored order as Square's ``Order`` JSON, absent optionals omitted."""
    currency = order.currency
    total = order_total(order)
    zero = money(0, currency)
    total_money = money(total, currency)
    return OrderWire(
        id=order.id,
        location_id=order.location_id,
        reference_id=order.reference_id,
        customer_id=order.customer_id,
        ticket_name=order.ticket_name,
        source_name=order.source_name,
        line_items=tuple(project_line_item(item, currency) for item in order.line_items),
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
        total_tip_money=zero,
        total_service_charge_money=zero,
        net_amounts=NetAmountsWire(
            total_money=total_money,
            tax_money=zero,
            discount_money=zero,
            tip_money=zero,
            service_charge_money=zero,
        ),
        # "net_amount_due_money" never goes negative: over-tendering an order
        # leaves nothing due rather than owing the buyer money, which is what
        # `Math.max(0, ...)` says in the reference.
        net_amount_due_money=money(max(0, total - tendered_total(order)), currency),
    ).wire()


def project_order_entry(order: OrderEntity) -> dict[str, Any]:
    """A stored order as an ``OrderEntry``."""
    return OrderEntryWire(order_id=order.id, version=order.version, location_id=order.location_id).wire()


# ---------------------------------------------------------------------------
# Requests. See the module docstring for the absent/null rule and for why every
# model below is strict.
# ---------------------------------------------------------------------------

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)


def supplied(model: BaseModel, field: str) -> bool:
    """Whether ``field`` was present in the request document at all.

    The one legitimate reading of "did the caller mention this?", and the
    reason the sparse models exist. ``model.field is None`` cannot answer it:
    an absent ``reference_id`` and ``"reference_id": null`` both validate to
    ``None``, and they mean "leave it alone" and "clear it" respectively.
    """
    return field in model.model_fields_set


class MoneyRequest(BaseModel):
    """``Money`` as a caller may send it.

    ``currency`` is optional here and required on Square's object. The order's
    location supplies it when the caller omits it, which is the only currency
    an order in this unit can be in -- the reference took the same currency
    from the location and never looked at the one on the request at all.
    https://developer.squareup.com/reference/square/objects/Money
    """

    model_config = _REQUEST

    amount: int
    currency: str | None = None

    def entity(self, fallback_currency: str) -> Money:
        return Money(amount=self.amount, currency=self.currency or fallback_currency)


class OrderSourceRequest(BaseModel):
    """``order.source``. Only ``name`` is modelled; Square's object has no
    other writable field.
    https://developer.squareup.com/reference/square/objects/OrderSource
    """

    model_config = _REQUEST

    name: str | None = None


class LineItemRequest(BaseModel):
    """One entry of ``order.line_items``, on create or on a sparse update.

    Every field is optional *here* because the same model serves both modes:
    CreateOrder requires ``quantity`` and a price, UpdateOrder requires neither
    on a line it is merely amending, and encoding that in the model would put
    the mode rule in two places. The surface enforces it.

    ``quantity`` is a **string** on Square's wire
    (https://developer.squareup.com/reference/square/objects/OrderLineItem),
    so ``"2"`` is right and ``2`` is a type error here rather than a silently
    coerced 2.
    """

    model_config = _REQUEST

    uid: str | None = None
    name: str | None = None
    quantity: str | None = None
    note: str | None = None
    catalog_object_id: str | None = None
    variation_name: str | None = None
    base_price_money: MoneyRequest | None = None


class NewOrderRequest(BaseModel):
    """``order`` on CreateOrder.

    https://developer.squareup.com/reference/square/orders-api/create-order
    """

    model_config = _REQUEST

    location_id: str = Field(min_length=1)
    reference_id: str | None = None
    customer_id: str | None = None
    ticket_name: str | None = None
    #: OPEN (the default) or DRAFT; the surface refuses the two terminal values.
    state: str | None = None
    source: OrderSourceRequest | None = None
    line_items: list[LineItemRequest] | None = None
    metadata: dict[str, str] | None = None


class CreateOrderRequest(BaseModel):
    """``POST /v2/orders``. ``idempotency_key`` is read by the kernel from the
    route's :class:`~vendorfake.core.kernel.types.IdempotencySpec`, and is
    declared here only so that it is documented in one place with the rest of
    the body."""

    model_config = _REQUEST

    order: NewOrderRequest
    idempotency_key: str | None = None


class OrderPatch(BaseModel):
    """``order`` on UpdateOrder: the sparse half.

    ``version`` is typed optional and required by the surface, so that the
    documented sentence -- "Your request must include the order.version
    property set to the current version of the order"
    (https://developer.squareup.com/docs/orders-api/manage-orders/update-orders)
    -- is what a caller who omits it reads, rather than a generic
    field-is-required message.
    """

    model_config = _REQUEST

    version: int | None = None
    state: str | None = None
    reference_id: str | None = None
    customer_id: str | None = None
    ticket_name: str | None = None
    metadata: dict[str, str] | None = None
    line_items: list[LineItemRequest] | None = None


class UpdateOrderRequest(BaseModel):
    """``PUT /v2/orders/{order_id}``.

    ``fields_to_clear`` is top-level, beside ``order`` rather than inside it,
    which is where Square documents it.
    https://developer.squareup.com/reference/square/orders-api/update-order
    """

    model_config = _REQUEST

    order: OrderPatch
    fields_to_clear: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class PayOrderRequest(BaseModel):
    """``POST /v2/orders/{order_id}/pay``.

    ``order_version`` is "The version of the order being paid. If not supplied,
    the latest version will be paid"
    (https://developer.squareup.com/reference/square/orders-api/pay-order), so
    ``None`` means "no opinion" and is passed through as no ``expect_version``
    rather than as version 0.
    """

    model_config = _REQUEST

    idempotency_key: str | None = None
    order_version: int | None = None
    payment_ids: list[str] | None = None


class TimeRangeRequest(BaseModel):
    """A ``TimeRange``: start inclusive, end exclusive.
    https://developer.squareup.com/reference/square/objects/TimeRange
    """

    model_config = _REQUEST

    start_at: str | None = None
    end_at: str | None = None


class DateTimeFilterRequest(BaseModel):
    """``query.filter.date_time_filter``.

    "If you use the DateTimeFilter in a SearchOrders query, you must set the
    sort_field in OrdersSort to the same field you filter for."
    https://developer.squareup.com/reference/square/objects/SearchOrdersDateTimeFilter

    Which of the three was supplied is read with :func:`supplied`, because that
    rule is about presence and not about value.
    """

    model_config = _REQUEST

    created_at: TimeRangeRequest | None = None
    updated_at: TimeRangeRequest | None = None
    closed_at: TimeRangeRequest | None = None


class StateFilterRequest(BaseModel):
    """``query.filter.state_filter``.
    https://developer.squareup.com/reference/square/objects/SearchOrdersStateFilter
    """

    model_config = _REQUEST

    states: list[str] = Field(default_factory=list)


class SearchOrdersFilterRequest(BaseModel):
    """``query.filter``."""

    model_config = _REQUEST

    state_filter: StateFilterRequest | None = None
    date_time_filter: DateTimeFilterRequest | None = None


class OrdersSortRequest(BaseModel):
    """``query.sort``. Defaults are Square's: CREATED_AT, DESC.
    https://developer.squareup.com/reference/square/objects/SearchOrdersSort
    """

    model_config = _REQUEST

    sort_field: str | None = None
    sort_order: str | None = None


class SearchOrdersQueryRequest(BaseModel):
    """``query``. ``filter`` shadows no attribute of ``BaseModel``; it is
    Square's field name and is spelled as Square spells it."""

    model_config = _REQUEST

    filter: SearchOrdersFilterRequest | None = None
    sort: OrdersSortRequest | None = None


class SearchOrdersRequest(BaseModel):
    """``POST /v2/orders/search``.

    ``limit`` default 500 and maximum 1000, ``location_ids`` maximum 10, all
    from https://developer.squareup.com/reference/square/orders-api/search-orders
    -- and all enforced in the surface, because the limit clamp belongs to the
    core's paginator and the location cap carries a documented message.
    """

    model_config = _REQUEST

    location_ids: list[str] | None = None
    query: SearchOrdersQueryRequest | None = None
    cursor: str | None = None
    limit: int | None = None
    #: "If set to true, returns the OrderEntry objects instead of Order objects."
    return_entries: bool = False


class BatchRetrieveOrdersRequest(BaseModel):
    """``POST /v2/orders/batch-retrieve``.

    https://developer.squareup.com/reference/square/orders-api/batch-retrieve-orders
    """

    model_config = _REQUEST

    order_ids: list[str]
    #: Deprecated on Square's own object, and optional: "omit it to retrieve
    #: orders within the scope of the current authorization's merchant ID".
    location_id: str | None = None
