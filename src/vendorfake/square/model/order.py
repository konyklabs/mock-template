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

SHRINK (prototype): taxes, discounts, service charges, returns and refunds are
not modelled. The corresponding roll-up fields are emitted as zero money so a
consumer deserialising the full ``Order`` shape still works, and
``net_amounts`` is therefore always equal to ``total_money``. Fulfillments are
modelled -- type, state and the three details objects -- and ``entries`` (the
``ENTRY_LIST`` application) are not: every fulfillment here covers the whole
order.

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

Empty arrays, in one rule
-------------------------
This package had two contradictory answers to "what does an empty list look
like on the wire": :class:`OrderWire` omitted ``line_items`` and ``tenders``
when empty, while SearchOrders emitted ``"orders": []`` on a search that
matched nothing -- the same question, answered both ways, in one vendor. Square
settles neither case: it publishes no sentence about empty arrays, and the only
adjacent text is SearchOrders' "The list is populated only if `return_entries`
is set to `false`", which is about ``return_entries`` and not about emptiness.
**NOT VERIFIED**, therefore, and stated here once as this unit's convention:

* an optional array **inside an entity** -- ``line_items``, ``tenders`` -- is
  *absent* when it is empty, exactly like every other absent optional, because
  "absence is absence" is the rule the rest of this file, the entity digest and
  the journal's ``changed`` list all run on, and an order with no tenders has
  no more tenders than it has a ``closed_at``;
* the collection **an operation returns** -- ``orders`` on SearchOrders and on
  BatchRetrieveOrders, ``order_entries`` -- is always present, empty when there
  was nothing to return, because it is the answer to the request rather than a
  property of an object, and "there were no matches" is a result a consumer
  must be able to read without also handling a missing key.

Both halves are pinned by tests, so the two cannot drift apart again.

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

    Key order is Square's, and it is checkable. It was previously described
    here as "the reference's, which is the order Square's CreateOrder example
    prints"; that was simply untrue -- the reference led with
    ``catalog_object_id`` and ``variation_name``, which Square's example does
    not -- and an unverifiable citation in a package whose whole discipline is
    that citations are checkable is worse than none.

    The two published orders differ, so both are named and one is followed:

    * the identifying fields are in the order the object's own field listing
      prints them -- ``uid, name, quantity, quantity_unit, note,
      catalog_object_id, catalog_version, variation_name, ...``
      (https://developer.squareup.com/reference/square/objects/OrderLineItem),
      which is the only published order that carries ``note`` and
      ``catalog_object_id`` at all;
    * the money roll-ups are in the order the CreateOrder example *response*
      prints them -- ``base_price_money, gross_sales_money, total_tax_money,
      total_service_charge_money, total_discount_money, total_money,
      variation_total_price_money``
      (https://developer.squareup.com/reference/square/orders-api/create-order)
      -- because that is the block a consumer actually reads back, and the
      field listing disagrees with the example about it.

    JSON key order is not semantic and no consumer may depend on it, so this
    buys nothing but legibility. It is the citation that had to be right.
    """

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
    """One ``Tender``. ``tip_money`` is the one optional, present when a tip
    was taken: "amount_money: The total amount of the tender, including
    `tip_money`" and "tip_money: The tip's amount of the tender".
    https://developer.squareup.com/reference/square/objects/Tender
    """

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
    """One ``Fulfillment``. The details object is emitted only when present,
    and only the one named for the type is ever stored.
    https://developer.squareup.com/reference/square/objects/Fulfillment
    """

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
        """The order as JSON, with every absent optional omitted.

        ``line_items``, ``fulfillments`` and ``tenders`` are omitted when empty
        rather than sent as ``[]`` -- the entity half of the one empty-array
        rule in the module docstring, which also says why the envelope half
        goes the other way and that Square documents neither. ``source`` is a
        nested object built from a single stored name.
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

    JUDGMENT -- a **negative** quantity is accepted and produces a negative
    line total, and therefore a negative ``total_money`` on the order. Square's
    ``quantity`` is a string documented only as "The count, or measurement, of
    a line item being purchased" with a length range and no sign rule
    (https://developer.squareup.com/reference/square/objects/OrderLineItem),
    and no published error covers it, so **NOT VERIFIED**: this unit neither
    refuses it nor floors the money at zero, and a consumer must not read a
    negative total here as something Square would return. ``net_amount_due``
    does clamp at zero, so such an order reports a negative total with nothing
    due -- see :func:`project_order`.

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
    """The sum of the line totals, in minor units -- what the order is *for*,
    before any tip a buyer adds at payment."""
    return sum(line_item_total(item) for item in order.line_items)


def tendered_total(order: OrderEntity) -> int:
    """Everything the tenders carry, tips included, in minor units."""
    return sum(tender.amount_money.amount for tender in order.tenders)


def tips_total(order: OrderEntity) -> int:
    """The tips the tenders carry, in minor units: ``total_tip_money``."""
    return sum(0 if tender.tip_money is None else tender.tip_money.amount for tender in order.tenders)


def amount_due(order: OrderEntity) -> int:
    """What is still owed on the line items: the order total less what the
    tenders have *applied* to it, which is their amount without the tip.

    One definition, used by every check that asks "can this payment take
    this much?" -- CreatePayment, CompletePayment, PayOrder -- and by the
    projection's ``net_amount_due_money``. A tip never reduces what is due
    and never counts toward completing the order.

    Clamped at zero. No route can tender past the total any more -- the
    Payments surface refuses past the due, PayOrder tenders exactly the due,
    and UpdateOrder refuses to shrink an order below what its tenders applied
    (the tendered floor) -- so the clamp is reachable only from a scenario
    that seeds tenders exceeding its lines, and there it reports nothing due
    rather than owing the buyer money.
    """
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
    # JUDGMENT -- `total_money` includes the tips the tenders collected.
    # "total_money: The total amount of money to collect for the order" and
    # "total_tip_money: The total tip amount of money to collect for the
    # order" (https://developer.squareup.com/reference/square/objects/Order),
    # and a tender's amount "including `tip_money`", so tenders reconcile to
    # `total_money` exactly when the order is paid. NOT VERIFIED that Square
    # rolls a payment-time tip into the order's `total_money` rather than
    # reporting it in `total_tip_money` alone; the two fields are emitted so a
    # consumer can compute either reading.
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
        # JUDGMENT, twice over, and NOT VERIFIED both times.
        #
        # First: this key is emitted on every order, and Square's own
        # CreateOrder and PayOrder example responses do not carry it. It is a
        # documented read-only `Order` field -- "The net amount of money due on
        # the order" -- and Square publishes no rule about when it is present,
        # so a consumer must not read its presence here as proof Square always
        # sends it. It is always sent because a fake that omits a computable
        # field teaches nothing, and it is the one number an unpaid order is
        # about.
        #
        # Second: it never goes negative -- see `amount_due`. Over-tendering
        # leaves nothing due rather than owing the buyer money, which is what
        # `Math.max(0, ...)` says in the reference; the Payments surface now
        # refuses to tender past what is due, so only a seed can reach it.
        # Note the asymmetry this leaves with a NEGATIVE
        # quantity, which this unit accepts because Square's `quantity` text
        # forbids no such thing (see `line_item_total`): such an order reports
        # a negative `total_money` and nothing due.
        net_amount_due_money=money(amount_due(order), currency),
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

    The maximum lengths are Square's, from the same page: ``uid`` "Max Length
    60", ``name`` "Max Length 512", ``note`` "Max Length 2000", ``quantity``
    "Max Length 12". They are enforced because they are documented, and a fake
    that accepts a 200-character ``uid`` teaches a consumer that Square will.

    The documented *minimum* on ``quantity`` -- "Min Length 1" -- is deliberately
    not spelled here. An empty ``quantity`` has two meanings on this surface,
    "you did not send one" on create and "clear it" on update, and each already
    has its own error naming the field; a ``min_length`` in the model would
    answer both of them with a third.
    """

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
"""The three details models below list every field their reference page
documents as writable-or-readable, so that a consumer's request round-trips
field for field; a key not on the page is dropped, per ``extra="ignore"``,
rather than stored as though Square would keep it."""


class FulfillmentRecipientRequest(BaseModel):
    """``recipient`` on any of the three details objects.
    https://developer.squareup.com/reference/square/objects/FulfillmentRecipient
    """

    model_config = _DETAILS

    customer_id: str | None = None
    display_name: str | None = None
    email_address: str | None = None
    phone_number: str | None = None
    address: dict[str, Any] | None = None


class PickupDetailsRequest(BaseModel):
    """``pickup_details``, every documented field.
    https://developer.squareup.com/reference/square/objects/FulfillmentPickupDetails

    JUDGMENT -- the ``*_at`` stamps are accepted from the caller. Square's
    reference marks several of them read-only (``placed_at``, ``accepted_at``,
    ``ready_at``, ``picked_up_at``, ...) and stamps them itself as the
    fulfillment moves; a consumer nonetheless sends ``picked_up_at`` alongside
    ``state: COMPLETED`` and expects to read it back. This unit stores what was
    sent and stamps only what was not -- see
    :func:`vendorfake.square.surface.orders._stamp_transition`. Which fields
    the real API would silently ignore is NOT VERIFIED.
    """

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
    The same JUDGMENT on the ``*_at`` stamps as :class:`PickupDetailsRequest`.
    """

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
    https://developer.squareup.com/reference/square/objects/FulfillmentShipmentDetails
    """

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
    """One entry of ``order.fulfillments``, on create or on a sparse update.

    Every field optional for the reason :class:`LineItemRequest` gives: the
    same model serves both modes, and the surface enforces that a new
    fulfillment names a ``type``. ``state`` on create defaults to
    ``PROPOSED``, the machine's initial state.
    https://developer.squareup.com/reference/square/objects/Fulfillment
    """

    model_config = _REQUEST

    uid: str | None = Field(default=None, max_length=60)
    type: str | None = None
    state: str | None = None
    pickup_details: PickupDetailsRequest | None = None
    delivery_details: DeliveryDetailsRequest | None = None
    shipment_details: ShipmentDetailsRequest | None = None


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
    fulfillments: list[FulfillmentRequest] | None = None
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
    fulfillments: list[FulfillmentRequest] | None = None


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

    #: Required, and required in the surface rather than here: "Your request
    #: must include one or more `location_ids`. `SearchOrders` only returns the
    #: orders for those locations."
    #: https://developer.squareup.com/docs/orders-api/manage-orders/search-orders
    #: Typed optional so that an omitted list and an empty one reach the same
    #: check and produce the same error, instead of Pydantic reporting one of
    #: them in its own vocabulary.
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
