"""The sale wire shapes: what a request may carry, what a sale is stored as,
and what the two response envelopes print.

DOCUMENTED (``api-2026-07``): ``line_items`` and ``payments`` are inline
arrays on the sale itself -- there is no ``/sales/{sale_id}/payments`` or
``/sales/{sale_id}/line_items`` sub-resource in this version of the
specification, which is the single most important shape fact about this tag.
The vendor's own spelling differs between the two outlet fields: the line
item's is ``fulfilment_outlet_id`` (one ``l``), the sale's is
``fulfillment_outlet_id`` (two) -- both reproduced exactly, because hiding the
typo would hide what a consumer meets in production.

JUDGMENT: ``version`` is emitted twice -- at ``_metadata.version``, where the
``Sale`` schema declares it, and at the top level, because the pagination
contract every list in this API follows requires reading the next ``after``
off the rows themselves (https://x-series-api.lightspeedhq.com/docs/pagination)
and ``initReturnSale``'s own example prints it there too. The two are always
the same number.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.util.json import compact
from vendorfake.lightspeed.entities import OBJECT_VERSION, SaleEntity
from vendorfake.lightspeed.model.money import to_amount, to_minor, to_number

__all__ = [
    "LINE_ITEM_STATUS_CONFIRMED",
    "SaleLineItemRequest",
    "SalePaymentRequest",
    "SaleRequest",
    "SaleUpdateRequest",
    "aggregate_payments_by_type",
    "build_line_item",
    "build_payment",
    "compute_taxes",
    "compute_totals",
    "project_sale",
]

_REQUEST = ConfigDict(extra="ignore", frozen=True)
"""A member this slice does not model (``service_fields``,
``fulfillment_details``, ...) is ignored, not refused."""

LINE_ITEM_STATUS_CONFIRMED = "CONFIRMED"
"""``SaleLineItem.status``: ``CONFIRMED`` on a pending sale is read-only."""


# -- request models ---------------------------------------------------------
#
# Money/quantity are typed `Any` rather than `float`: `model/money.py`'s
# `to_minor` is this package's one amount reader, and raises the vendor's 422
# naming the exact dotted field instead of Pydantic's own coercion errors.


class LineItemProductRef(BaseModel):
    """``LineItemProduct``: ``{id}``, required."""

    model_config = _REQUEST

    id: str = Field(min_length=1)


class LineItemPricingRequest(BaseModel):
    """``LineItemPricing``. Only ``price`` is required."""

    model_config = _REQUEST

    price: Any = None
    cost: Any = None
    discount: Any = None
    loyalty_amount: Any = None
    adjustments: list[dict[str, Any]] = Field(default_factory=list)


class LineItemTaxRequest(BaseModel):
    """``LineItemTax``: ``id`` and ``amount``, both required."""

    model_config = _REQUEST

    id: str = Field(min_length=1)
    amount: Any = None


class LineItemMetadataRequest(BaseModel):
    """``LineItemMetadata``: ``sequence``, ``is_price_override``, ``fulfillment_method``."""

    model_config = _REQUEST

    sequence: int | None = None
    is_price_override: bool | None = None
    fulfillment_method: str | None = None


class LineItemSourceRequest(BaseModel):
    """``LineItemSource``: the salesperson and the register that added the line."""

    model_config = _REQUEST

    author_id: str | None = None
    register_id: str | None = None


class SaleLineItemRequest(BaseModel):
    """``SaleLineItem``. ``product``, ``quantity``, ``pricing`` and ``tax`` are the four required members."""

    model_config = _REQUEST

    product: LineItemProductRef
    quantity: Any = None
    pricing: LineItemPricingRequest
    tax: LineItemTaxRequest
    id: str | None = None
    status: str | None = None
    #: The vendor's own single-``l`` spelling; see the module docstring.
    fulfilment_outlet_id: str | None = None
    source: LineItemSourceRequest | None = None
    attributes: list[dict[str, Any]] = Field(default_factory=list)
    metadata_: LineItemMetadataRequest | None = Field(default=None, alias="_metadata")


class PaymentTypeConfigRequest(BaseModel):
    """``PaymentTypeConfig``: ``{config_id}``, required -- the payment type id."""

    model_config = _REQUEST

    config_id: str = Field(min_length=1)


class PaymentSourceRequest(BaseModel):
    """``PaymentSource``: ``{register_id}``."""

    model_config = _REQUEST

    register_id: str | None = None


class SalePaymentRequest(BaseModel):
    """``SalePayment``. ``amount`` and ``type`` are the documented required pair."""

    model_config = _REQUEST

    amount: Any = None
    type: PaymentTypeConfigRequest
    id: str | None = None
    date: str | None = None
    source: PaymentSourceRequest | None = None


class SaleSourceRequest(BaseModel):
    """``SaleRequestSource``. ``author_id`` (the one required member) is not
    resolved against anything -- the Users tag is out of scope; recorded in
    ``capabilities.py`` under ``sale-author-not-resolved``."""

    model_config = _REQUEST

    author_id: str = Field(min_length=1)
    register_id: str | None = None
    id: str | None = None
    type: str | None = None


class SalePricingRequest(BaseModel):
    """``SalePricing``: sale-level ``adjustments``. Accepted, not applied."""

    model_config = _REQUEST

    adjustments: list[dict[str, Any]] = Field(default_factory=list)


class SaleUpdateRequest(BaseModel):
    """``SaleUpdateRequest`` == ``SaleRequestBase``: ``source``/``state`` required, everything else optional."""

    model_config = _REQUEST

    source: SaleSourceRequest
    state: str = Field(min_length=1)
    line_items: list[SaleLineItemRequest] = Field(default_factory=list)
    payments: list[SalePaymentRequest] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)
    customer_id: str | None = None
    date: str | None = None
    note: str | None = None
    short_code: str | None = None
    invoice_number: str | None = None
    accounts_transaction_id: str | None = None
    fulfillment_outlet_id: str | None = None
    pricing: SalePricingRequest | None = None


class SaleRequest(SaleUpdateRequest):
    """``SaleRequest``: the update body plus an optional caller-supplied ``id`` (else one is generated)."""

    model_config = _REQUEST

    id: str | None = None


# -- building the stored rows ------------------------------------------------


def build_line_item(
    request: SaleLineItemRequest,
    *,
    line_id: str,
    sequence: int,
    field: str,
    is_return: bool = False,
) -> dict[str, Any]:
    """One stored line item: ids and text as given, money in minor units.
    ``field`` is this line's dotted request path (``line_items[0]``), for the
    refusal naming ``line_items[0].pricing.price``. Also runs ``_scale``'s
    overflow check here, before anything commits.
    """
    quantity = _quantity(request.quantity, field=f"{field}.quantity", allow_negative=is_return)
    metadata = request.metadata_
    # The caller's own `_metadata.sequence` wins where it gives one.
    ordinal = sequence if metadata is None or metadata.sequence is None else metadata.sequence
    line = compact(
        {
            "id": line_id,
            "product_id": request.product.id,
            "quantity": quantity,
            "price_minor": to_minor(request.pricing.price, field=f"{field}.pricing.price"),
            "cost_minor": _optional_minor(request.pricing.cost, field=f"{field}.pricing.cost"),
            "discount_minor": _optional_minor(request.pricing.discount, field=f"{field}.pricing.discount"),
            "loyalty_minor": _optional_minor(request.pricing.loyalty_amount, field=f"{field}.pricing.loyalty_amount"),
            "tax_id": request.tax.id,
            "tax_minor": to_minor(request.tax.amount, field=f"{field}.tax.amount"),
            "status": request.status,
            "fulfilment_outlet_id": request.fulfilment_outlet_id,
            "sequence": ordinal,
            "is_price_override": None if metadata is None else metadata.is_price_override,
            "is_return": is_return or None,
        }
    )
    _line_money(line, field=field)
    if "cost_minor" in line:
        _scale(_minor(line, "cost_minor"), quantity, field=f"{field}.pricing.cost")
    return line


def build_payment(
    request: SalePaymentRequest,
    *,
    payment_id: str,
    payment_type_id: str,
    register_id: str,
    date: str,
    field: str,
    allow_negative: bool = False,
) -> dict[str, Any]:
    """One stored payment. The caller has already resolved the payment type and
    the register -- both refuse with the vendor's payment-error body rather
    than plain field validation, in ``surface/sales.py``."""
    return compact(
        {
            "id": payment_id,
            "payment_type_id": payment_type_id,
            "amount_minor": to_minor(request.amount, field=f"{field}.amount", allow_negative=allow_negative),
            "register_id": register_id,
            "date": date,
        }
    )


def _quantity(value: Any, *, field: str, allow_negative: bool) -> float:
    """``SaleLineItem.quantity``, required. JUDGMENT: zero is refused, and a
    negative quantity is refused unless the sale is a return."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be a number.",
            field=field,
            info={"supplied": value if isinstance(value, str | int | float) else str(value)},
        )
    quantity = float(value)
    if quantity != quantity or quantity in (float("inf"), float("-inf")):  # NaN or infinity
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{field} must be a finite number.", field=field)
    if quantity == 0 or (quantity < 0 and not allow_negative):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"{field} must be greater than zero; a negative quantity belongs to a return sale, which is "
                f"created by POST /sales/{{sale_id}}/actions/return."
            ),
            field=field,
            info={"supplied": quantity},
        )
    return quantity


def _optional_minor(value: Any, *, field: str) -> int | None:
    """An optional amount: absent stays absent, present is read strictly."""
    return None if value is None else to_minor(value, field=field, allow_negative=True)


# -- computing what a caller cannot send ------------------------------------


def _line_money(line: Mapping[str, Any], *, field: str) -> tuple[float, int, int, int, int]:
    """``(quantity, price, discount, tax, loyalty)`` in minor units, rounded
    once per line -- so a receipt's line totals sum to the sale total a
    consumer's own arithmetic reproduces. ``field`` is the dotted request path
    this line sits at, as in :func:`build_line_item`.
    """
    quantity = float(line.get("quantity", 0) or 0)
    price = _minor(line, "price_minor")
    discount = _minor(line, "discount_minor")
    tax = _minor(line, "tax_minor")
    loyalty = _minor(line, "loyalty_minor")
    return (
        quantity,
        _scale(price, quantity, field=f"{field}.pricing.price"),
        _scale(discount, quantity, field=f"{field}.pricing.discount"),
        _scale(tax, quantity, field=f"{field}.tax.amount"),
        _scale(loyalty, quantity, field=f"{field}.pricing.loyalty_amount"),
    )


def _minor(line: Mapping[str, Any], key: str) -> int:
    value = line.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _scale(minor: int, quantity: float, *, field: str) -> int:
    """``minor x quantity`` back to whole minor units, half-up. Guards the
    same overflow ``money.to_minor``/``scalars.decimal_text`` do -- each
    operand passes its own validator but their product can still exceed the
    decimal context's precision -- raising the documented invalid-value
    refusal rather than a raw 500 (konyklabs/roadmap#41).
    """
    try:
        return int((Decimal(minor) * Decimal(str(quantity))).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"{field} multiplied by the line's quantity is larger than this API can express in whole minor units."
            ),
            field=field,
            info={"quantity": quantity},
        ) from None


def compute_totals(line_items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``SaleTotals`` from the stored line items. ``price`` is tax-exclusive;
    ``surcharge`` is always ``0.0`` -- this unit has no promotions machinery."""
    price = 0
    tax = 0
    loyalty = 0
    for index, line in enumerate(line_items):
        _, line_price, line_discount, line_tax, line_loyalty = _line_money(line, field=f"line_items[{index}]")
        price += line_price - line_discount
        tax += line_tax
        loyalty += line_loyalty
    return {
        "price": to_number(price),
        "price_incl_tax": to_number(price + tax),
        "tax": to_number(tax),
        "loyalty": to_number(loyalty),
        "surcharge": to_number(0),
    }


def compute_taxes(line_items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``SaleResponseTax[]``: per-line tax totals grouped by ``LineItemTax.id``, in first-seen order."""
    totals: dict[str, int] = {}
    for index, line in enumerate(line_items):
        tax_id = str(line.get("tax_id", ""))
        if not tax_id:
            continue
        _, _, _, line_tax, _ = _line_money(line, field=f"line_items[{index}]")
        totals[tax_id] = totals.get(tax_id, 0) + line_tax
    return [{"id": tax_id, "tax": to_number(minor)} for tax_id, minor in totals.items()]


def aggregate_payments_by_type(
    payments: Iterable[Mapping[str, Any]],
    *,
    names: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Stored sale payments summed per payment type, as
    ``RegisterPaymentSummaryPaymentType`` (``total`` a decimal string). A
    payment naming a type the retailer no longer has is counted under the
    empty name rather than dropped."""
    totals: dict[str, int] = {}
    for payment in payments:
        payment_type_id = str(payment.get("payment_type_id", ""))
        if not payment_type_id:
            continue
        totals[payment_type_id] = totals.get(payment_type_id, 0) + _minor(payment, "amount_minor")
    return [
        {
            "payment_type_id": payment_type_id,
            "payment_type_name": names.get(payment_type_id, ""),
            "total": to_amount(minor),
        }
        for payment_type_id, minor in totals.items()
    ]


# -- the wire ----------------------------------------------------------------


def project_line_item(line: Mapping[str, Any], *, field: str = "line_items[]") -> dict[str, Any]:
    """``SaleResponseLineItem``: the request's shape plus the totals
    ``LineItemResponsePricing``/``LineItemResponseTax`` add."""
    quantity, price_total, discount_total, tax_total, loyalty_total = _line_money(line, field=field)
    cost = _minor(line, "cost_minor")
    pricing = compact(
        {
            "price": to_number(_minor(line, "price_minor")),
            "total": to_number(price_total - discount_total),
            "cost": to_number(cost) if "cost_minor" in line else None,
            "cost_total": (
                to_number(_scale(cost, quantity, field=f"{field}.pricing.cost")) if "cost_minor" in line else None
            ),
            "discount": to_number(_minor(line, "discount_minor")) if "discount_minor" in line else None,
            "discount_total": to_number(discount_total) if "discount_minor" in line else None,
            "loyalty_amount": to_number(_minor(line, "loyalty_minor")) if "loyalty_minor" in line else None,
            "loyalty_amount_total": to_number(loyalty_total) if "loyalty_minor" in line else None,
        }
    )
    metadata = compact({"sequence": line.get("sequence"), "is_price_override": line.get("is_price_override")})
    return compact(
        {
            "id": line.get("id"),
            "product": {"id": line.get("product_id")},
            "quantity": quantity,
            "pricing": pricing,
            "tax": {
                "id": line.get("tax_id"),
                "amount": to_number(_minor(line, "tax_minor")),
                "total": to_number(tax_total),
            },
            "status": line.get("status"),
            "fulfilment_outlet_id": line.get("fulfilment_outlet_id"),
            # `LineItemReturn` only on a return's lines, matching the vendor's examples.
            "return": {"is_return": True} if line.get("is_return") else None,
            "_metadata": metadata or None,
        }
    )


def project_payment(payment: Mapping[str, Any], *, names: Mapping[str, str]) -> dict[str, Any]:
    """``SaleResponsePayment``. ``type`` is a ``PaymentTypeDetails``, carrying
    the payment type's ``name``."""
    payment_type_id = str(payment.get("payment_type_id", ""))
    return compact(
        {
            "id": payment.get("id"),
            "amount": to_number(_minor(payment, "amount_minor")),
            "date": payment.get("date"),
            "type": compact({"config_id": payment_type_id, "name": names.get(payment_type_id)}),
            "source": compact({"register_id": payment.get("register_id")}),
        }
    )


def project_sale(entity: Mapping[str, Any], *, names: Mapping[str, str] | None = None) -> dict[str, Any]:
    """One stored sale as the ``Sale`` schema puts it on the wire. ``names``
    maps payment type id to name; optional because the webhook mapper
    projects a sale with no store in hand."""
    sale = SaleEntity.from_entity(entity)
    lookup = names or {}
    line_items = [project_line_item(line, field=f"line_items[{index}]") for index, line in enumerate(sale.line_items)]
    source = compact(
        {
            "id": sale.source.get("id"),
            "type": sale.source.get("type"),
            "outlet_id": sale.source.get("outlet_id"),
            "register_id": sale.source.get("register_id"),
            "author": compact({"id": sale.source.get("author_id")}) or None,
        }
    )
    returns = compact(
        {
            "is_return": sale.is_return,
            "original_sale_id": sale.original_sale_id,
            "return_sale_ids": list(sale.return_sale_ids) or None,
        }
    )
    version = _minor_free_version(entity)
    return compact(
        {
            "id": sale.id,
            "state": sale.state,
            "customer_id": sale.customer_id,
            "note": sale.note,
            "short_code": sale.short_code,
            "invoice_number": sale.invoice_number,
            "receipt_number": sale.receipt_number,
            "accounts_transaction_id": sale.accounts_transaction_id,
            "attributes": list(sale.attributes),
            "source": source or None,
            "line_items": line_items,
            "payments": [project_payment(payment, names=lookup) for payment in sale.payments],
            "taxes": compute_taxes(sale.line_items),
            "totals": compute_totals(sale.line_items),
            "return": returns,
            "date": sale.date or None,
            "created_at": sale.created_at or None,
            "updated_at": sale.updated_at or None,
            "deleted_at": sale.deleted_at,
            "_metadata": {"version": version},
            # The second half of the twice-emitted version; see the module docstring.
            "version": version,
        }
    )


def _minor_free_version(entity: Mapping[str, Any]) -> int:
    value = entity.get(OBJECT_VERSION)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
