"""The order wire vocabulary: Clover's ``Order`` and ``LineItem``, as models,
plus the request shapes the orders surface parses and the two arithmetic
helpers -- projection with expansions, and the atomic-order total.

FOR: stating once what a Clover order document carries -- field names, units,
enums, defaults -- so the surfaces parse and project through one vocabulary
and tests can pin it without a unit.

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
  ``1500`` and an absent ``unitQty`` means one unit.
* **``percentageDecimal`` is percent x 10000** on a service charge
  (https://docs.clover.com/dev/docs/ordercreateatomicorder), so 18% is
  ``180000``. A discount carries either a negative ``amount`` in cents (the
  tutorial's ``-200``) or a whole-number ``percentage``.

``total`` is client-owned on plain orders -- DOCUMENTED, and the fidelity
point the surface must not soften: "Order totals are calculated dynamically
and updated by the app the merchant uses... If your app modifies an order, it
must update the total as well" (creating-custom-orders). The model treats
``total`` as an ordinary stored integer; only :func:`atomic_total`, used by the
two ``/atomic_order/*`` endpoints that Clover documents as calculating totals,
ever computes one.

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
  ``groupLineItems``, ``printed``) are documented fields with no documented
  defaults, so they are optional-and-omitted rather than carrying invented
  values -- except line items' ``exchanged`` and ``refunded`` (default False:
  a freshly created line has been neither, a JUDGMENT about initial state,
  not a claimed Clover default).

Expansions -- ``expand=lineItems,discounts,orderType,serviceCharge`` and the
dotted ``lineItems.discounts``, "maximum of three fields per API call"
(https://docs.clover.com/dev/docs/expanding-fields). JUDGMENT on what an
*unexpanded* order shows: the nested collections ``lineItems``,
``discounts`` and ``serviceCharge`` are omitted unless asked for, which is
what "expand" has to mean for a field to be expandable at all; ``orderType``
is a reference and shows its ``{"id"}`` either way, because this unit stores
nothing more about an order type. Expanded ``lineItems`` are capped at 100:
"Offsets and limits cannot be used to paginate results in nested fields"
(https://docs.clover.com/dev/docs/paginating-elements), and 100 is the
documented nested cap.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import js_round

__all__ = [
    "EXPANDABLE",
    "MAX_EXPANSIONS",
    "NESTED_CAP",
    "AtomicOrderRequest",
    "BulkLineItemsRequest",
    "DiscountRequest",
    "DiscountWire",
    "ItemRefWire",
    "LineItemRequest",
    "LineItemWire",
    "OrderCartRequest",
    "OrderCreateRequest",
    "OrderPatchRequest",
    "OrderTypeRefWire",
    "OrderWire",
    "PayType",
    "PaymentState",
    "ServiceChargeRequest",
    "ServiceChargeWire",
    "atomic_total",
    "line_total",
    "project_order",
    "supplied",
]

_REQUEST = ConfigDict(extra="ignore", frozen=True)
"""The parse-path configuration, and both halves are deliberate.

``extra="ignore"`` because a documented Clover order carries far more than
this build models -- ``isVat``, ``unpaidBalance``, ``employee``,
``customers`` and more are all real fields on the order reference -- and
refusing a body because it mentioned one of them would fail on the shrink
rather than on the thing under test.

Not strict, because these models validate *decoded* documents (python mode):
under strict validation ``"paymentState": "PAID"`` would refuse to become the
enum member and a JSON array would refuse to become the ``lineItems`` tuple,
so every documented body in Clover's own examples would 400. The money
guarantee survives without strictness: lax ``int`` still refuses a fractional
``20.99``, which is the coercion that would actually corrupt an amount.
"""

EXPANDABLE: frozenset[str] = frozenset({"lineItems", "discounts", "orderType", "serviceCharge", "lineItems.discounts"})
"""The expansions this unit accepts; see the module docstring."""

MAX_EXPANSIONS = 3
""""maximum of three fields per API call" (expanding-fields)."""

NESTED_CAP = 100
"""Nested arrays are not pageable and stop at 100 (paginating-elements)."""


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


# ---------------------------------------------------------------------------
# Wire shapes.
# ---------------------------------------------------------------------------


class ItemRefWire(BaseModel):
    """A reference to an inventory item, as a line item carries it.

    "either a ``price`` or an ``item`` object with an inventory item ``id``"
    (https://docs.clover.com/dev/docs/ordercreatelineitem).
    """

    model_config = _REQUEST

    id: str

    def wire(self) -> dict[str, Any]:
        return {"id": self.id}


class OrderTypeRefWire(BaseModel):
    """A reference to an order type, as the documented create example carries
    it: ``{"orderType": {"id": "KFRPRVCZ73JHM"}, ...}``
    (https://docs.clover.com/dev/docs/creating-custom-orders)."""

    model_config = _REQUEST

    id: str

    def wire(self) -> dict[str, Any]:
        return {"id": self.id}


class DiscountWire(BaseModel):
    """A discount: a negative ``amount`` in cents, or a ``percentage``
    (https://docs.clover.com/dev/docs/create-an-atomic-order, whose tutorial
    sends ``"amount": -200``)."""

    model_config = _REQUEST

    id: str | None = None
    name: str | None = None
    amount: int | None = None
    percentage: int | None = None

    def wire(self) -> dict[str, Any]:
        return compact({"id": self.id, "name": self.name, "amount": self.amount, "percentage": self.percentage})


class ServiceChargeWire(BaseModel):
    """A service charge; ``percentageDecimal`` is percent x 10000."""

    model_config = _REQUEST

    id: str | None = None
    name: str | None = None
    percentageDecimal: int = 0
    enabled: bool | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "percentageDecimal": self.percentageDecimal,
                "enabled": self.enabled,
            }
        )


class LineItemWire(BaseModel):
    """One line item. ``price`` in integer cents; ``unitQty`` fixed-point x1000."""

    model_config = _REQUEST

    id: str
    price: int
    name: str | None = None
    note: str | None = None
    unitQty: int | None = None
    #: Documented field, undocumented default: optional-and-omitted, like the
    #: order's own flag fields (module docstring).
    printed: bool | None = None
    exchanged: bool = False
    refunded: bool = False
    item: ItemRefWire | None = None
    discounts: tuple[DiscountWire, ...] = ()

    def wire(self, *, with_discounts: bool = True) -> dict[str, Any]:
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
                "discounts": (
                    [discount.wire() for discount in self.discounts] if with_discounts and self.discounts else None
                ),
            }
        )


class OrderWire(BaseModel):
    """A whole order, ready to serialise. Field set from the order reference
    pages; see the module docstring for units, enums and defaults."""

    model_config = _REQUEST

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
    discounts: tuple[DiscountWire, ...] = ()
    serviceCharge: ServiceChargeWire | None = None

    def wire(self, *, expand: Iterable[str] = ()) -> dict[str, Any]:
        """The order as JSON, absent optionals omitted, nested collections
        present only when expanded (module docstring). An empty expanded
        array is omitted rather than sent as ``[]``, per the package rule."""
        wanted = frozenset(expand)
        lines = self.lineItems[:NESTED_CAP] if "lineItems" in wanted else ()
        line_discounts = "lineItems.discounts" in wanted
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
                "lineItems": [line.wire(with_discounts=line_discounts) for line in lines] if lines else None,
                "discounts": (
                    [discount.wire() for discount in self.discounts[:NESTED_CAP]]
                    if "discounts" in wanted and self.discounts
                    else None
                ),
                "serviceCharge": (
                    self.serviceCharge.wire() if "serviceCharge" in wanted and self.serviceCharge is not None else None
                ),
            }
        )


def project_order(entity: Mapping[str, Any], expand: Iterable[str] = ()) -> dict[str, Any]:
    """A stored order as Clover JSON, with the requested expansions.

    The stored document uses the wire's own field names, so the entity
    validates straight into :class:`OrderWire` (``extra="ignore"`` drops the
    unit's internal ``merchant_id``/``version``/``created_at`` keys).
    """
    return OrderWire.model_validate(entity).wire(expand=expand)


# ---------------------------------------------------------------------------
# Requests.
# ---------------------------------------------------------------------------


def supplied(model: BaseModel, field: str) -> bool:
    """Whether ``field`` was present in the request document at all -- the one
    legitimate reading of "did the caller mention this?" for a sparse update.
    ``model.field is None`` cannot answer it: an absent ``note`` and
    ``"note": null`` both validate to ``None``."""
    return field in model.model_fields_set


class DiscountRequest(BaseModel):
    """A discount as a caller sends it; ``amount`` (negative cents) or
    ``percentage``. Both absent is refused by the surface."""

    model_config = _REQUEST

    name: str | None = None
    amount: int | None = None
    percentage: int | None = None


class ServiceChargeRequest(BaseModel):
    """``percentageDecimal`` = percent x 10000."""

    model_config = _REQUEST

    name: str | None = None
    percentageDecimal: int = 0
    enabled: bool | None = None


class LineItemRequest(BaseModel):
    """One line item on create, bulk create, or inside an ``orderCart``.

    "either a ``price`` or an ``item`` object with an inventory item ``id``"
    -- both optional *here* because the rule is a disjunction the surface
    enforces, with its documented message.
    """

    model_config = _REQUEST

    price: int | None = None
    item: ItemRefWire | None = None
    name: str | None = None
    note: str | None = None
    unitQty: int | None = None
    printed: bool | None = None
    discounts: list[DiscountRequest] | None = None


class BulkLineItemsRequest(BaseModel):
    """``POST .../bulk_line_items``: ``{"items": [...]}``, "max 100", "Each
    item must include a price" (https://docs.clover.com/dev/docs/orderbulkcreatelineitems).
    JUDGMENT on the wrapper key: the reference page's body is an ``items``
    array; the response echoes the same key."""

    model_config = _REQUEST

    items: list[LineItemRequest] = Field(default_factory=list)


class OrderCreateRequest(BaseModel):
    """``POST .../orders`` -- the documented create example is
    ``{"orderType":{"id":...},"currency":"USD","total":1500,"state":"Open"}``
    and every field is optional on the wire; a missing ``currency`` and a
    missing ``total`` are defaulted by the surface (labelled there)."""

    model_config = _REQUEST

    currency: str | None = None
    total: int | None = None
    state: str | None = None
    paymentState: PaymentState | None = None
    payType: PayType | None = None
    clientCreatedTime: int | None = None
    title: str | None = None
    note: str | None = None
    externalReferenceId: str | None = None
    testMode: bool | None = None
    taxRemoved: bool | None = None
    manualTransaction: bool | None = None
    groupLineItems: bool | None = None
    orderType: OrderTypeRefWire | None = None


class OrderPatchRequest(OrderCreateRequest):
    """``POST .../orders/{orderId}`` -- the same fields, applied sparsely:
    only what the caller mentioned changes (see :func:`supplied`)."""


class OrderCartRequest(BaseModel):
    """The ``orderCart`` an atomic endpoint takes
    (https://docs.clover.com/dev/docs/ordercreateatomicorder)."""

    model_config = _REQUEST

    orderType: OrderTypeRefWire | None = None
    currency: str | None = None
    title: str | None = None
    note: str | None = None
    externalReferenceId: str | None = None
    lineItems: list[LineItemRequest] = Field(default_factory=list)
    discounts: list[DiscountRequest] | None = None
    serviceCharge: ServiceChargeRequest | None = None


class AtomicOrderRequest(BaseModel):
    """``POST .../atomic_order/orders`` and ``.../checkouts``: an
    ``orderCart`` wrapper."""

    model_config = _REQUEST

    orderCart: OrderCartRequest


# ---------------------------------------------------------------------------
# Arithmetic: the atomic total. Everything here is pure.
# ---------------------------------------------------------------------------


def _discount_total(discounts: Sequence[Mapping[str, Any]], base: int) -> int:
    """Discounts applied to ``base`` cents: each is its (negative) ``amount``
    or ``-percentage%`` of ``base``. Percentages are of the undiscounted
    base, not compounded (JUDGMENT; Clover documents no compounding rule)."""
    total = 0
    for discount in discounts:
        amount = discount.get("amount")
        percentage = discount.get("percentage")
        if isinstance(amount, int) and not isinstance(amount, bool):
            total += amount
        elif isinstance(percentage, int) and not isinstance(percentage, bool):
            total -= js_round(base * percentage / 100)
    return total


def line_total(line: Mapping[str, Any]) -> int:
    """``price x unitQty / 1000``, plus the line's own discounts, in cents.

    ``unitQty`` absent means one unit (1000). JUDGMENT on rounding: half-up
    on cents through :func:`~vendorfake.core.util.numbers.js_round`, since
    Clover documents the units and not the rounding of a fractional cent.
    """
    price = line.get("price")
    base_price = price if isinstance(price, int) and not isinstance(price, bool) else 0
    qty = line.get("unitQty")
    unit_qty = qty if isinstance(qty, int) and not isinstance(qty, bool) else 1000
    base = js_round(base_price * unit_qty / 1000)
    discounts = line.get("discounts") or []
    return base + _discount_total(discounts, base)


def atomic_total(
    lines: Sequence[Mapping[str, Any]],
    discounts: Sequence[Mapping[str, Any]] = (),
    service_charge: Mapping[str, Any] | None = None,
) -> int:
    """The total an ``/atomic_order/*`` endpoint computes, in cents.

    ``sum(line totals) + order discounts + service charge``, where the service
    charge is ``percentageDecimal / 10000`` percent of the discounted subtotal
    (JUDGMENT on the base: Clover documents the unit of ``percentageDecimal``
    and not what it applies to; a charge on the discounted amount is what a
    receipt shows). Taxes are out of scope (``CLOVER_NOT_MODELED``).
    """
    subtotal = sum(line_total(line) for line in lines)
    discounted = subtotal + _discount_total(discounts, subtotal)
    charge = 0
    if service_charge is not None:
        decimal = service_charge.get("percentageDecimal")
        if isinstance(decimal, int) and not isinstance(decimal, bool):
            charge = js_round(discounted * decimal / 10000 / 100)
    return discounted + charge
