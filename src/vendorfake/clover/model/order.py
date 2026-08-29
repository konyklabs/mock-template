"""The order wire vocabulary: Clover's ``Order`` and ``LineItem``, as models.

FOR: stating once what a Clover order document carries -- field names, units,
enums, defaults -- so the PR-C surfaces parse and project through one
vocabulary and tests can pin it without a unit.

INVARIANT: **an absent optional emits no key.** Every projection is assembled
through the core's ``compact()``, so an order with no ``note`` carries no
``"note": null`` -- a consumer writing ``if "note" in order`` must take the
same branch it would against the real API's sparse documents.

Units, both documented and easy to confuse:

* **Money is integer cents.** "$20.99 is represented as an amount value of
  2099" (https://docs.clover.com/dev/docs/creating-custom-orders); ``currency``
  is ISO-4217.
* **Entity timestamps are Unix milliseconds** -- ``createdTime``,
  ``modifiedTime``, ``clientCreatedTime``, ``deletedTime`` (the inventory
  create example shows ``modifiedTime: 1755786102000``). OAuth expirations are
  Unix *seconds*; see ``model/oauth.py``.
* **``unitQty`` is fixed-point x1000** -- "unit quantity multiplied by 1000"
  (https://docs.clover.com/dev/docs/ordercreatelineitem), so 1.5 units is
  ``1500``.

``total`` is client-owned on plain orders -- DOCUMENTED, and the fidelity
point PR C must not soften: "Order totals are calculated dynamically and
updated by the app the merchant uses... If your app modifies an order, it must
update the total as well" (creating-custom-orders). The model therefore treats
``total`` as an ordinary stored integer and provides no recomputation.

Enums and defaults:

* ``state`` is ``open``, ``locked`` or absent/null ("null is the default for
  hidden orders") -- kept a plain ``str`` here because Clover's own pages mix
  ``Open``/``open`` and storage is verbatim; the machine in ``machine.py``
  owns the canonical values.
* ``paymentState`` values are documented:
  OPEN|PAID|REFUNDED|CREDITED|PARTIALLY_PAID|PARTIALLY_REFUNDED. JUDGMENT --
  the ``OPEN`` *default* is this project's reading (an order nobody has paid
  is open for payment); Clover documents the values, not an initial one.
* ``payType`` values are documented: SPLIT_GUEST|SPLIT_ITEM|SPLIT_CUSTOM|FULL.
* The boolean flags (``taxRemoved``, ``testMode``, ``manualTransaction``,
  ``groupLineItems``) are documented fields with no documented defaults, so
  they are optional-and-omitted here rather than carrying invented values --
  except where PR A's contract fixes one (line items' ``exchanged`` and
  ``refunded`` default False: a freshly created line has been neither, which
  is a JUDGMENT about initial state, not a claimed Clover default).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from vendorfake.core.util.json import compact

__all__ = [
    "ItemRefWire",
    "LineItemWire",
    "OrderTypeRefWire",
    "OrderWire",
    "PayType",
    "PaymentState",
]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Strict, so a money amount that arrived as ``20.99`` is refused here rather
than coerced to a wrong integer on the way to the wire. Cents are whole
numbers by definition."""


class PaymentState(StrEnum):
    """The six documented ``paymentState`` values, and no others."""

    OPEN = "OPEN"
    PAID = "PAID"
    REFUNDED = "REFUNDED"
    CREDITED = "CREDITED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class PayType(StrEnum):
    """The four documented ``payType`` values."""

    SPLIT_GUEST = "SPLIT_GUEST"
    SPLIT_ITEM = "SPLIT_ITEM"
    SPLIT_CUSTOM = "SPLIT_CUSTOM"
    FULL = "FULL"


class ItemRefWire(BaseModel):
    """A reference to an inventory item, as a line item carries it.

    "either a ``price`` or an ``item`` object with an inventory item ``id``"
    (https://docs.clover.com/dev/docs/ordercreatelineitem).
    """

    model_config = _WIRE

    id: str

    def wire(self) -> dict[str, Any]:
        return {"id": self.id}


class OrderTypeRefWire(BaseModel):
    """A reference to an order type, as the documented create example carries
    it: ``{"orderType": {"id": "KFRPRVCZ73JHM"}, ...}``
    (https://docs.clover.com/dev/docs/creating-custom-orders)."""

    model_config = _WIRE

    id: str

    def wire(self) -> dict[str, Any]:
        return {"id": self.id}


class LineItemWire(BaseModel):
    """One line item. ``price`` in integer cents; ``unitQty`` fixed-point x1000."""

    model_config = _WIRE

    id: str
    price: int
    name: str | None = None
    note: str | None = None
    unitQty: int | None = None
    printed: bool = False
    exchanged: bool = False
    refunded: bool = False
    item: ItemRefWire | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "price": self.price,
                "note": self.note,
                "unitQty": self.unitQty,
                "printed": self.printed,
                "exchanged": self.exchanged,
                "refunded": self.refunded,
                "item": None if self.item is None else self.item.wire(),
            }
        )


class OrderWire(BaseModel):
    """A whole order, ready to serialise. Field set from the order reference
    pages; see the module docstring for units, enums and defaults."""

    model_config = _WIRE

    id: str
    currency: str
    #: Client-owned on plain orders; never recomputed. Integer cents.
    total: int
    #: ``open``/``locked`` verbatim as stored, or None for a hidden order.
    state: str | None = None
    paymentState: PaymentState = PaymentState.OPEN
    payType: PayType | None = None
    createdTime: int | None = None
    modifiedTime: int | None = None
    clientCreatedTime: int | None = None
    deletedTime: int | None = None
    title: str | None = None
    note: str | None = None
    externalReferenceId: str | None = None
    testMode: bool | None = None
    taxRemoved: bool | None = None
    manualTransaction: bool | None = None
    groupLineItems: bool | None = None
    orderType: OrderTypeRefWire | None = None
    lineItems: tuple[LineItemWire, ...] = ()

    def wire(self) -> dict[str, Any]:
        """The order as JSON, with every absent optional omitted.

        ``lineItems`` is omitted when empty rather than sent as ``[]``,
        following the same absence-is-absence convention the Square package
        settled on -- Clover, like Square, publishes no sentence about empty
        arrays, so this is the project's one rule rather than fidelity.
        Clover's own list envelope wraps element arrays as ``{"elements":
        [...]}``; that envelope belongs to the list *endpoints* (PR C), not to
        the entity.
        """
        return compact(
            {
                "id": self.id,
                "currency": self.currency,
                "total": self.total,
                "state": self.state,
                "paymentState": self.paymentState.value,
                "payType": None if self.payType is None else self.payType.value,
                "title": self.title,
                "note": self.note,
                "externalReferenceId": self.externalReferenceId,
                "testMode": self.testMode,
                "taxRemoved": self.taxRemoved,
                "manualTransaction": self.manualTransaction,
                "groupLineItems": self.groupLineItems,
                "createdTime": self.createdTime,
                "modifiedTime": self.modifiedTime,
                "clientCreatedTime": self.clientCreatedTime,
                "deletedTime": self.deletedTime,
                "orderType": None if self.orderType is None else self.orderType.wire(),
                "lineItems": [item.wire() for item in self.lineItems] if self.lineItems else None,
            }
        )
