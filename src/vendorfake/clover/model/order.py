"""The order wire vocabulary: Clover's ``Order`` and ``LineItem`` models, the request shapes
the orders surface parses, and two arithmetic helpers -- projection with expansions, and the
atomic-order total.

INVARIANT: an absent optional emits no key -- every projection goes through the core's
``compact()``, so a sparse document matches the real API's.

Units: money is integer cents, DOCUMENTED ("$20.99 is represented as an amount value of 2099",
https://docs.clover.com/dev/docs/creating-custom-orders); ``currency`` is ISO-4217. Entity
timestamps are Unix milliseconds; OAuth expirations are Unix seconds (``model/oauth.py``).
``unitQty`` is fixed-point x1000, DOCUMENTED ("unit quantity multiplied by 1000",
https://docs.clover.com/dev/docs/ordercreatelineitem) -- absent means one unit.
``percentageDecimal`` is percent x 10000 on a service charge, DOCUMENTED
(https://docs.clover.com/dev/docs/ordercreateatomicorder).

``total`` is client-owned on plain orders, DOCUMENTED ("If your app modifies an order, it must
update the total as well", creating-custom-orders); only :func:`atomic_total` ever computes one.

``state`` is kept a plain ``str`` since Clover's own pages mix case (``machine.py`` owns the
canonical values). Boolean flags with no documented default stay optional-and-omitted, except
line items' ``exchanged``/``refunded`` (JUDGMENT default False).

Expansions max three fields per call, DOCUMENTED (https://docs.clover.com/dev/docs/expanding-fields).
Unexpanded, nested collections are omitted (JUDGMENT); expanded ``lineItems`` cap at 100, the
documented nested-pagination limit (https://docs.clover.com/dev/docs/paginating-elements).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import js_round

__all__ = [
    "EXPANDABLE",
    "MAX_EXPANSIONS",
    "NESTED_CAP",
    "TAX_RATE_SCALE",
    "AtomicOrderRequest",
    "AtomicTotals",
    "BulkLineItemsRequest",
    "DiscountRequest",
    "DiscountWire",
    "ItemRefWire",
    "LineItemRequest",
    "LineItemWire",
    "ModificationRequest",
    "ModificationWire",
    "ModifierRefWire",
    "OrderCartRequest",
    "OrderCreateRequest",
    "OrderPatchRequest",
    "OrderTypeRefWire",
    "OrderWire",
    "PayType",
    "PaymentState",
    "RefRequest",
    "RefWire",
    "ServiceChargeRequest",
    "ServiceChargeWire",
    "atomic_total",
    "atomic_totals",
    "line_total",
    "project_order",
    "supplied",
]

_REQUEST = ConfigDict(extra="ignore", frozen=True)
"""``extra="ignore"``: a real order carries more fields than this build
models. Not strict: validates decoded documents into enum members."""

EXPANDABLE: frozenset[str] = frozenset(
    {
        "lineItems",
        "discounts",
        "orderType",
        "serviceCharge",
        "employee",
        "customers",
        "payments",
        "lineItems.discounts",
        "lineItems.modifications",
    }
)
"""The expansions this unit accepts; ``orderType``/``employee`` show their ``{"id"}`` either way."""

TAX_RATE_SCALE = 100000
"""JUDGMENT, NOT VERIFIED -- percent x 100000 (7.25% is ``725000``). Clover
types ``rate`` as an integer with no stated scale (https://docs.clover.com/dev/reference/taxrategettaxrates)."""

MAX_EXPANSIONS = 3
"""DOCUMENTED: "maximum of three fields per API call" (expanding-fields, cited in the module docstring)."""

NESTED_CAP = 100
"""DOCUMENTED: nested arrays are not pageable and stop at 100 (paginating-elements)."""


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
    """DOCUMENTED: a reference to an inventory item (https://docs.clover.com/dev/docs/ordercreatelineitem)."""

    model_config = _REQUEST

    id: str = Field(min_length=1)

    def wire(self) -> dict[str, Any]:
        return {"id": self.id}


class OrderTypeRefWire(BaseModel):
    """DOCUMENTED: a reference to an order type (https://docs.clover.com/dev/docs/creating-custom-orders)."""

    model_config = _REQUEST

    id: str = Field(min_length=1)

    def wire(self) -> dict[str, Any]:
        return {"id": self.id}


class DiscountWire(BaseModel):
    """DOCUMENTED: a negative ``amount`` in cents, or a ``percentage`` (https://docs.clover.com/dev/docs/create-an-atomic-order)."""

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


class RefWire(BaseModel):
    """A bare ``{"id"}`` reference -- an employee, a customer, a payment."""

    model_config = _REQUEST

    id: str

    def wire(self) -> dict[str, Any]:
        return {"id": self.id}


class ModifierRefWire(BaseModel):
    """DOCUMENTED: the modifier a modification points at (https://docs.clover.com/dev/docs/create-an-atomic-order)."""

    model_config = _REQUEST

    id: str
    name: str | None = None
    available: bool | None = None

    def wire(self) -> dict[str, Any]:
        return compact({"id": self.id, "name": self.name, "available": self.available})


class ModificationWire(BaseModel):
    """One line-item modification: ``{"modifier": {...}, "amount": 25}``.
    ``amount`` in cents, per unit (JUDGMENT: scales with ``unitQty``)."""

    model_config = _REQUEST

    id: str | None = None
    name: str | None = None
    amount: int = 0
    modifier: ModifierRefWire | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "amount": self.amount,
                "modifier": None if self.modifier is None else self.modifier.wire(),
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
    #: Undocumented default; optional-and-omitted (module docstring).
    printed: bool | None = None
    exchanged: bool = False
    refunded: bool = False
    item: ItemRefWire | None = None
    discounts: tuple[DiscountWire, ...] = ()
    modifications: tuple[ModificationWire, ...] = ()

    def wire(self, *, with_discounts: bool = True, with_modifications: bool = True) -> dict[str, Any]:
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
                "modifications": (
                    [modification.wire() for modification in self.modifications]
                    if with_modifications and self.modifications
                    else None
                ),
            }
        )


class OrderWire(BaseModel):
    """A whole order, ready to serialise -- see the module docstring for
    units, enums and defaults."""

    model_config = _REQUEST

    id: str
    currency: str
    #: Client-owned; never recomputed.
    total: int = Field(ge=0)
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
    employee: RefWire | None = None
    customers: tuple[RefWire, ...] = ()
    lineItems: tuple[LineItemWire, ...] = ()
    discounts: tuple[DiscountWire, ...] = ()
    serviceCharge: ServiceChargeWire | None = None
    payments: tuple[RefWire, ...] = ()

    def wire(self, *, expand: Iterable[str] = ()) -> dict[str, Any]:
        """The order as JSON; nested collections appear only when expanded
        (module docstring). An empty expanded array is omitted, not ``[]``."""
        wanted = frozenset(expand)
        lines = self.lineItems[:NESTED_CAP] if "lineItems" in wanted else ()
        line_discounts = "lineItems.discounts" in wanted
        line_modifications = "lineItems.modifications" in wanted
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
                "employee": None if self.employee is None else self.employee.wire(),
                "customers": (
                    [customer.wire() for customer in self.customers[:NESTED_CAP]]
                    if "customers" in wanted and self.customers
                    else None
                ),
                "lineItems": (
                    [line.wire(with_discounts=line_discounts, with_modifications=line_modifications) for line in lines]
                    if lines
                    else None
                ),
                "discounts": (
                    [discount.wire() for discount in self.discounts[:NESTED_CAP]]
                    if "discounts" in wanted and self.discounts
                    else None
                ),
                "serviceCharge": (
                    self.serviceCharge.wire() if "serviceCharge" in wanted and self.serviceCharge is not None else None
                ),
                "payments": (
                    [payment.wire() for payment in self.payments[:NESTED_CAP]]
                    if "payments" in wanted and self.payments
                    else None
                ),
            }
        )


def project_order(entity: Mapping[str, Any], expand: Iterable[str] = ()) -> dict[str, Any]:
    """A stored order as Clover JSON, with the requested expansions.
    ``extra="ignore"`` drops the unit's internal storage keys."""
    return OrderWire.model_validate(entity).wire(expand=expand)


# ---------------------------------------------------------------------------
# Requests.
# ---------------------------------------------------------------------------


def supplied(model: BaseModel, field: str) -> bool:
    """Whether ``field`` was present in the request at all: ``.field is None`` can't
    tell, since an absent ``note`` and ``"note": null`` both validate to ``None``."""
    return field in model.model_fields_set


class DiscountRequest(BaseModel):
    """A discount as sent: ``amount`` (negative cents) or ``percentage``; both absent is refused by the surface."""

    model_config = _REQUEST

    name: str | None = None
    amount: int | None = None
    percentage: int | None = Field(default=None, ge=0, le=100)


class ServiceChargeRequest(BaseModel):
    """``percentageDecimal`` = percent x 10000. An ``id`` alone means the merchant's default charge."""

    model_config = _REQUEST

    id: str | None = None
    name: str | None = None
    percentageDecimal: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


class RefRequest(BaseModel):
    """``{"id": ...}`` as a caller sends a reference."""

    model_config = _REQUEST

    id: str = Field(min_length=1)


class ModificationRequest(BaseModel):
    """``{"modifier": {"id"}, "amount"?}``; a missing ``amount`` is the modifier's own price."""

    model_config = _REQUEST

    modifier: RefRequest
    #: A reduction is a discount, not a modification.
    amount: int | None = Field(default=None, ge=0)
    name: str | None = None


class LineItemRequest(BaseModel):
    """One line item on create/bulk-create/``orderCart``: ``price``/``item`` are both optional
    here; the surface enforces the disjunction."""

    model_config = _REQUEST

    price: int | None = Field(default=None, ge=0)
    item: ItemRefWire | None = None
    name: str | None = None
    note: str | None = None
    #: JUDGMENT: negative has no documented meaning; a return isn't a line item.
    unitQty: int | None = Field(default=None, ge=0)
    printed: bool | None = None
    discounts: list[DiscountRequest] | None = None
    modifications: list[ModificationRequest] | None = None
    #: For a bare-price line; an item-backed line takes the item's rates.
    taxRates: list[RefRequest] | None = None


class BulkLineItemsRequest(BaseModel):
    """DOCUMENTED: ``POST .../bulk_line_items`` body (https://docs.clover.com/dev/docs/orderbulkcreatelineitems)."""

    model_config = _REQUEST

    items: list[LineItemRequest] = Field(default_factory=list)


class OrderCreateRequest(BaseModel):
    """``POST .../orders``: every field is optional; a missing ``currency``/``total`` is surface-defaulted."""

    model_config = _REQUEST

    currency: str | None = None
    total: int | None = Field(default=None, ge=0)
    state: str | None = None
    #: Refused by the surface unless OPEN: payments move it (JUDGMENT).
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
    employee: RefRequest | None = None
    customers: list[RefRequest] | None = None


class OrderPatchRequest(OrderCreateRequest):
    """``POST .../orders/{orderId}``: the same fields, applied sparsely (see :func:`supplied`)."""


class OrderCartRequest(BaseModel):
    """The ``orderCart`` an atomic endpoint takes (https://docs.clover.com/dev/docs/ordercreateatomicorder)."""

    model_config = _REQUEST

    orderType: OrderTypeRefWire | None = None
    currency: str | None = None
    title: str | None = None
    note: str | None = None
    externalReferenceId: str | None = None
    employee: RefRequest | None = None
    customers: list[RefRequest] | None = None
    lineItems: list[LineItemRequest] = Field(default_factory=list)
    discounts: list[DiscountRequest] | None = None
    serviceCharge: ServiceChargeRequest | None = None


class AtomicOrderRequest(BaseModel):
    """``POST .../atomic_order/orders`` and ``.../checkouts``: an ``orderCart`` wrapper."""

    model_config = _REQUEST

    orderCart: OrderCartRequest


# ---------------------------------------------------------------------------
# Arithmetic: the atomic total. Everything here is pure.
# ---------------------------------------------------------------------------


def _discount_total(discounts: Sequence[Mapping[str, Any]], base: int) -> int:
    """Each discount is its (negative) ``amount`` or ``-percentage%`` of ``base``. JUDGMENT: percentages
    are of the undiscounted base, not compounded."""
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
    """``price x unitQty / 1000`` plus the line's own discounts, in cents; ``unitQty`` absent means
    one unit. JUDGMENT: half-up rounding, floored at zero."""
    price = line.get("price")
    base_price = price if isinstance(price, int) and not isinstance(price, bool) else 0
    qty = line.get("unitQty")
    unit_qty = qty if isinstance(qty, int) and not isinstance(qty, bool) else 1000
    per_unit = base_price + sum(_int_or_zero(m.get("amount")) for m in line.get("modifications") or [])
    base = js_round(per_unit * unit_qty / 1000)
    discounts = line.get("discounts") or []
    return max(0, base + _discount_total(discounts, base))


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def atomic_total(
    lines: Sequence[Mapping[str, Any]],
    discounts: Sequence[Mapping[str, Any]] = (),
    service_charge: Mapping[str, Any] | None = None,
) -> int:
    """The pre-tax total an ``/atomic_order/*`` endpoint computes: ``sum(line totals) + order
    discounts + service charge`` (JUDGMENT). Tax is added by :func:`atomic_totals`."""
    subtotal = sum(line_total(line) for line in lines)
    # JUDGMENT: floored at zero rather than a negative order.
    discounted = max(0, subtotal + _discount_total(discounts, subtotal))
    return discounted + _service_charge_amount(discounted, service_charge)


def _service_charge_amount(base: int, service_charge: Mapping[str, Any] | None) -> int:
    if service_charge is None or service_charge.get("enabled") is False:
        return 0
    decimal = service_charge.get("percentageDecimal")
    if isinstance(decimal, int) and not isinstance(decimal, bool):
        return js_round(base * decimal / 10000 / 100)
    return 0


@dataclass(frozen=True, slots=True)
class AtomicTotals:
    """The documented checkout response's top level (https://docs.clover.com/dev/reference/ordercheckoutatomicorder)."""

    subtotal: int
    total: int
    totalTaxAmount: int
    taxSummaries: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        # A negative figure here is a calculator bug, never a real response.
        for name in ("subtotal", "total", "totalTaxAmount"):
            if getattr(self, name) < 0:
                raise ValueError(f"atomic {name} is negative: {getattr(self, name)}")

    def wire(self) -> dict[str, Any]:
        return {
            "subtotal": self.subtotal,
            "total": self.total,
            "totalTaxAmount": self.totalTaxAmount,
            "taxSummaries": [dict(summary) for summary in self.taxSummaries],
        }


def atomic_totals(
    lines: Sequence[Mapping[str, Any]],
    line_rates: Sequence[Sequence[Mapping[str, Any]]],
    discounts: Sequence[Mapping[str, Any]] = (),
    service_charge: Mapping[str, Any] | None = None,
) -> AtomicTotals:
    """The whole totals block for a cart; ``line_rates[i]`` are the tax rates for ``lines[i]``.
    JUDGMENT: tax is computed per line on its own discounted total."""
    subtotal = sum(line_total(line) for line in lines)
    pre_tax = atomic_total(lines, discounts, service_charge)
    summaries: dict[str, dict[str, Any]] = {}
    for line, rates in zip(lines, line_rates, strict=True):
        base = line_total(line)
        for rate in rates:
            scale = _int_or_zero(rate.get("rate"))
            amount = js_round(base * scale / TAX_RATE_SCALE / 100)
            key = str(rate.get("id", rate.get("name", "")))
            summary = summaries.setdefault(
                key, compact({"id": rate.get("id"), "name": rate.get("name"), "rate": scale, "amount": 0})
            )
            summary["amount"] += amount
    tax = sum(summary["amount"] for summary in summaries.values())
    return AtomicTotals(
        subtotal=subtotal,
        total=pre_tax + tax,
        totalTaxAmount=tax,
        taxSummaries=tuple(summaries.values()),
    )
