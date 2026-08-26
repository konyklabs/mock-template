"""Order projections: stored entity -> Square's wire JSON.

FOR: emitting exactly the document Square's own examples show, including the
read-only money roll-ups Square computes rather than accepts, so that a
consumer deserialising with Square's SDK gets the fields it expects.

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
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict

from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import js_parse_float, js_round
from vendorfake.square.entities import Money, OrderEntity, OrderLineItem, Tender

__all__ = [
    "LineItemWire",
    "MoneyWire",
    "NetAmountsWire",
    "OrderEntryWire",
    "OrderWire",
    "TenderWire",
    "line_item_total",
    "money",
    "order_total",
    "project_line_item",
    "project_order",
    "project_order_entry",
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
