"""The sale wire shapes: what a request may carry, what a sale is stored as,
and what the two response envelopes print.

FOR: keeping the whole of "what a sale looks like" in one module, so the
surface in ``surface/sales.py`` reads as routing and rules rather than as dict
building, and so the entity a ``sale.update`` webhook carries and the entity
``GET /sales/{sale_id}`` answers are produced by the same function.

DOCUMENTED, and read out of ``api-2026-07`` schema by schema:

``SaleRequestBase``
    The editable body, with ``source`` and ``state`` the only two ``required``
    members. ``line_items`` and ``payments`` are **inline arrays on the sale
    itself** -- there is no ``/sales/{sale_id}/payments`` and no
    ``/sales/{sale_id}/line_items`` sub-resource anywhere in this version of
    the specification, which is the single most important shape fact about
    this tag. ``SaleRequest`` adds an optional caller-supplied ``id``;
    ``SaleUpdateRequest`` is ``SaleRequestBase`` unchanged, with the id in the
    path.
``SaleLineItem``
    ``product`` (``{id}``), ``quantity``, ``pricing`` (``{price}`` required,
    plus ``cost``, ``discount``, ``loyalty_amount``, ``adjustments``) and
    ``tax`` (``{id, amount}``, both required) are the four ``required``
    members; ``id``, ``status``, ``fulfilment_outlet_id``, ``source``,
    ``attributes`` and ``_metadata`` are optional. Note the vendor's own
    spelling: the LINE ITEM's outlet field is ``fulfilment_outlet_id`` (one
    ``l``) while the SALE's is ``fulfillment_outlet_id`` (two). Both are
    reproduced exactly, because a fake that silently accepts the tidier
    spelling hides the typo a consumer will meet in production.
``SalePayment``
    ``amount`` and ``type`` are required; ``type`` is a ``PaymentTypeConfig``,
    i.e. ``{"config_id": "<payment type id>"}`` -- "Payment type id to be used
    for the payment" -- and the register is ``source.register_id``, "The ID of
    the register used to add this payment to the sale". So a payment names its
    payment type and its register through two nested one-member objects, not
    as flat ids.
``Sale`` (the response)
    ``line_items`` become ``SaleResponseLineItem`` (``pricing`` gains
    ``cost_total``/``discount_total``/``loyalty_amount_total``/``total``,
    ``tax`` gains ``total``), ``payments`` become ``SaleResponsePayment``
    (``type`` becomes ``PaymentTypeDetails``, which carries the payment type's
    ``name``), and the sale gains ``totals`` (``SaleTotals``), ``taxes``
    (``SaleResponseTax[]``), ``return`` (``SaleReturn``) and ``_metadata``
    (``SaleResponseMetadata``, whose ``version`` is "Monotonically increasing
    version number of the sale").

JUDGMENT, each labelled at its site below:

* **``version`` is emitted twice** -- at ``_metadata.version`` because the
  ``Sale`` schema declares it only there, and at the top level because the
  pagination contract every list in this API follows requires a caller to read
  the next ``after`` off the rows themselves
  (https://x-series-api.lightspeedhq.com/docs/pagination, "Every entity carries
  version") and because ``initReturnSale``'s own response example prints
  ``"version": 1978890425`` at the top level. The two are always the same
  number. Emitting only one of them would break either the schema or the
  documented walk.
* **the totals are computed, never taken from the request.** ``SaleTotals`` is
  absent from ``SaleRequestBase`` -- a caller cannot send one -- so every
  member is derived from the line items here. A fake that let a caller declare
  its own totals would be unable to show a consumer the rounding the real API
  does.
* **tax is per line item and inclusive of nothing.** ``LineItemTax.amount`` is
  "The unit tax value associated with this line item", so the line's tax total
  is ``amount x quantity`` and the sale's ``totals.tax`` is the sum of those;
  ``totals.price`` is documented "tax exclusive" and ``totals.price_incl_tax``
  is the two added. No tax RATE is read anywhere, because the Taxes tag is
  outside issue #94's scoped surface and a rate would have to be invented.
* **``totals.surcharge`` is always 0.** Surcharges arrive through
  ``SalePayment.surcharge`` (``SalePaymentSurcharge``) and the promotions and
  adjustment machinery this slice does not model; reporting a number this unit
  cannot compute would be worse than reporting the zero it can.
* **``pricing.adjustments`` and ``SalePricing.adjustments`` are accepted and
  do not change any total.** They are ``SaleAdjustment``/``LineItemAdjustment``
  arrays whose semantics (``NON_CASH_FEE``, ``DISCOUNT``, ``TIP``, each with an
  ``AdjustmentAmount`` and an ``AdjustmentSource``) belong to the promotions
  surface this slice does not serve. Recorded in ``capabilities.py`` under
  ``sale-adjustments`` rather than half-implemented.
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
"""``extra="ignore"``, as every request model in this package is: a consumer
sending a member of the schema this slice does not model (``service_fields``,
``fulfillment_details``, ``ecom_custom_charges``) gets the sale it asked for
rather than a 422 about a field the vendor really does accept."""

LINE_ITEM_STATUS_CONFIRMED = "CONFIRMED"
"""``SaleLineItem.status``: "If defined as ``CONFIRMED`` for pending sales, the
line item will be added as **read-only**"."""


# -- request models ---------------------------------------------------------
#
# Money and quantity are typed `Any` here rather than `float`, deliberately.
# Pydantic would coerce `"12.34"`, `true` and `Decimal` alike and report its own
# failure wording for the rest; `model/money.py`'s `to_minor` is this package's
# one amount reader and it raises the vendor's 422 naming the exact dotted
# field. Typing them loosely and converting in `build_*` below is what keeps
# one error vocabulary for every amount on this surface.


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
    """``LineItemMetadata``: ``sequence``, ``is_price_override``,
    ``fulfillment_method``."""

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
    """``SaleLineItem``. ``product``, ``quantity``, ``pricing`` and ``tax`` are
    the four documented ``required`` members."""

    model_config = _REQUEST

    product: LineItemProductRef
    quantity: Any = None
    pricing: LineItemPricingRequest
    tax: LineItemTaxRequest
    id: str | None = None
    status: str | None = None
    #: The vendor's own single-``l`` spelling on the LINE ITEM; see the module
    #: docstring.
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
    """``SaleRequestSource``. ``author_id`` is the one required member.

    ``author_id`` is NOT resolved against anything: the Users tag (``GET
    /users``, ``GET /users/{user_id}``) is outside issue #94's scoped surface,
    so this unit has no user collection to check it against and refusing an
    unknown cashier would be inventing a rule it cannot apply consistently.
    Recorded in ``capabilities.py`` under ``sale-author-not-resolved``.
    """

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
    """``SaleUpdateRequest`` == ``SaleRequestBase``: ``source`` and ``state``
    required, everything else optional."""

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
    """``SaleRequest``: the update body plus an optional caller-supplied ``id``
    ("If not included, one will be generated")."""

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

    ``field`` is the dotted path this line item sits at in the request body
    (``line_items[0]``), so a bad amount is refused naming
    ``line_items[0].pricing.price`` rather than "a price".

    THE LINE'S PRODUCTS ARE COMPUTED HERE AND THROWN AWAY, deliberately.
    ``_scale`` refuses an amount whose product with the quantity overflows the
    decimal context, and the only other place that product is taken is
    ``project_sale`` -- which runs AFTER the insert. A refusal raised there
    would answer the caller a correct 422 for a sale that is already in the
    store and already in the journal, so the check is pulled forward to where
    every other amount on this line is validated: before any id is minted and
    before anything commits.
    """
    quantity = _quantity(request.quantity, field=f"{field}.quantity", allow_negative=is_return)
    metadata = request.metadata_
    # The caller's own ordering wins where it gives one: `_metadata.sequence`
    # is "Order of the line item in the sale", and a caller that numbers its
    # lines is telling the receipt what order to print them in.
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
    the register, because both are refusals with the vendor's payment-error
    body rather than plain field validation -- see ``surface/sales.py``."""
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
    """``SaleLineItem.quantity``, ``format: double`` and required.

    JUDGMENT: zero is refused, and a negative quantity is refused on a sale
    that is not a return. ``initReturnSale``'s own example prints
    ``"quantity": -1`` on every line of a return, which is where negatives come
    from; a zero-quantity line is a line that sells nothing and is far more
    likely to be a caller's bug than an intention.
    """
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
    once per line.

    Rounded HERE and not at the end, because a receipt shows a total per line
    and the sale's total is the sum of what the receipt printed. Summing
    unrounded products and rounding once would produce a sale total a
    consumer's own line-by-line arithmetic cannot reproduce.

    ``field`` is the dotted path this line sits at in the request body
    (``line_items[0]``), so an amount that cannot be scaled is refused naming
    ``line_items[0].pricing.price`` rather than "a price" -- the same rule
    ``build_line_item`` follows for the amounts themselves.
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
    """``minor x quantity`` back to whole minor units, half-up.

    ``quantity`` is ``format: double`` -- a weighed item is ``0.35`` kg -- so
    the product needs rounding. Half-up, matching ``model/money.py``.

    THE GUARD IS THE ONE ``money.to_minor`` AND ``scalars.decimal_text``
    CARRY, and it is here for the same reason: each operand passes its own
    validator, but their PRODUCT can still need more than the decimal
    context's 28 significant digits (a legal price times a legal quantity),
    and an unguarded ``quantize`` then raises ``InvalidOperation`` out of the
    handler. The kernel shapes that as a 500 carrying the exception's own
    text, which konyklabs/roadmap#41 declared a defect class rather than an
    acceptable outcome for caller-supplied extremes: they answer the
    documented invalid-value refusal, naming the line item field that could
    not be scaled.
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
    """``SaleTotals`` from the stored line items.

    ``price`` is documented "tax exclusive" and ``price_incl_tax`` "tax
    inclusive", so the second is the first plus ``tax``. ``surcharge`` is
    always ``0.0``; see the module docstring.
    """
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
    """``SaleResponseTax[]``: one row per distinct tax id, in first-seen order.

    ``SaleResponseTax`` is ``{id, tax}`` -- "The total tax value for this tax"
    -- so this is the per-line tax totals grouped by ``LineItemTax.id``.
    First-seen order rather than sorted, so the rows follow the sale's own
    lines the way a printed receipt does.
    """
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
    """Stored sale payments summed per payment type, in the register payments
    summary's own wire shape.

    ``RegisterPaymentSummaryPaymentType`` is ``{payment_type_id,
    payment_type_name, total}`` and its ``total`` is a decimal **string** --
    the register summary is on the string side of this vendor's two money
    shapes even though the sale payments feeding it are numbers. Both
    conversions are ``model/money.py``'s.

    A payment naming a type this retailer no longer has is still counted, under
    the empty name: dropping money from a total because a lookup missed would
    make the summary silently wrong, which is the one thing a totals endpoint
    must not be.
    """
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
    """``SaleResponseLineItem``: the request's nested shape plus the totals
    ``LineItemResponsePricing`` and ``LineItemResponseTax`` add.

    ``field`` names this line's place in the body, for the refusal ``_scale``
    raises; ``project_sale`` passes the index."""
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
            # `LineItemReturn` is emitted only on a return's lines: a
            # `{"is_return": false}` on every line of every ordinary sale is
            # noise the vendor's own examples do not print.
            "return": {"is_return": True} if line.get("is_return") else None,
            "_metadata": metadata or None,
        }
    )


def project_payment(payment: Mapping[str, Any], *, names: Mapping[str, str]) -> dict[str, Any]:
    """``SaleResponsePayment``. ``type`` is a ``PaymentTypeDetails``, which is
    where the payment type's ``name`` reaches a consumer."""
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
    """One stored sale as the ``Sale`` schema puts it on the wire.

    ``names`` maps payment type id to name, for ``PaymentTypeDetails.name``. It
    is optional because the webhook mapper projects a sale with no store in
    hand; a delivery then carries the payment's ``config_id`` and no name,
    which is what the request itself carried.
    """
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
            # The second half of the twice-emitted version; see the module
            # docstring. `versioning.envelope` reads this member off the
            # PROJECTED rows to build the list envelope's max/min.
            "version": version,
        }
    )


def _minor_free_version(entity: Mapping[str, Any]) -> int:
    value = entity.get(OBJECT_VERSION)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
