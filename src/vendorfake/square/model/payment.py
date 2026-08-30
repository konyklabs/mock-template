"""The payment wire vocabulary: what CreatePayment accepts, and the ``Payment``
that goes back out.

FOR: emitting the document Square's CreatePayment example shows, restricted
to the one source this unit takes, and stating the request shapes as models
for the reasons :mod:`vendorfake.square.model.order` gives -- strict types, an
absent field distinguishable from a null one, and unknown fields ignored
rather than refused.

Shapes from https://developer.squareup.com/reference/square/objects/Payment
and the CreatePayment page
(https://developer.squareup.com/reference/square/payments-api/create-payment).
``external_details`` is
https://developer.squareup.com/reference/square/objects/ExternalPaymentDetails.

INVARIANT: **an absent optional emits no key**, through ``compact()``, as
everywhere in this package. A payment with no ``order_id`` has no ``order_id``
key; a consumer branching on ``"order_id" in payment`` takes the right branch.

What is emitted, and what is not
--------------------------------
``receipt_number`` and ``receipt_url`` appear only on a COMPLETED payment:
"receipt_url: The URL for the payment's receipt ... The field is only
populated for COMPLETED payments." ``approved_money`` is the amount on every
status but CANCELED, where nothing was approved -- JUDGMENT, from the field's
"The amount of money approved for this payment". ``version_token`` is "an
opaque token" derived from the store version, so a CompletePayment carrying a
stale one is the documented VERSION_MISMATCH.

SHRINK (prototype): ``delay_duration`` / ``delay_action`` / ``delayed_until``
(card-hold expiry), ``processing_fee``, ``refunded_money``, ``card_details``,
``cash_details``, ``risk_evaluation``, ``buyer_email_address``, the billing
and shipping addresses and ``capabilities`` are not emitted. None of them
applies to an external payment or changes a state this unit models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact, digest_of
from vendorfake.square.entities import PaymentEntity
from vendorfake.square.machine import PaymentState
from vendorfake.square.model.order import MoneyRequest, MoneyWire, money

__all__ = [
    "EXTERNAL_PAYMENT_TYPES",
    "EXTERNAL_SOURCE_ID",
    "SQUARE_PRODUCT",
    "CancelPaymentRequest",
    "CompletePaymentRequest",
    "CreatePaymentRequest",
    "ExternalDetailsRequest",
    "PaymentWire",
    "project_payment",
    "version_token_of",
]

EXTERNAL_SOURCE_ID = "EXTERNAL"
"""``source_id`` for a payment the seller took outside Square: "For an
external payment, `source_id` should be `EXTERNAL`" -- and the only source
this unit accepts."""

EXTERNAL_PAYMENT_TYPES: tuple[str, ...] = (
    "CHECK",
    "BANK_TRANSFER",
    "OTHER_GIFT_CARD",
    "CRYPTO",
    "SQUARE_CASH",
    "SOCIAL",
    "EXTERNAL",
    "EMONEY",
    "CARD",
    "STORED_BALANCE",
    "FOOD_VOUCHER",
    "OTHER",
)
"""``ExternalPaymentDetails.type``: "The type of external payment the seller
received", with these documented values.
https://developer.squareup.com/reference/square/objects/ExternalPaymentDetails
"""

SQUARE_PRODUCT = "ECOMMERCE_API"
"""``application_details.square_product`` for a payment taken through the
API. https://developer.squareup.com/reference/square/enums/ApplicationDetailsExternalSquareProduct"""

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)
_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)


class PaymentWire(BaseModel):
    """A ``Payment``, ready to serialise, in the documented field order."""

    model_config = _WIRE

    id: str
    created_at: str
    updated_at: str
    amount_money: MoneyWire
    tip_money: MoneyWire | None = None
    total_money: MoneyWire
    approved_money: MoneyWire
    status: str
    source_type: str
    location_id: str
    order_id: str | None = None
    reference_id: str | None = None
    customer_id: str | None = None
    note: str | None = None
    external_details: dict[str, Any] | None = None
    receipt_number: str | None = None
    receipt_url: str | None = None
    application_details: dict[str, str]
    version_token: str

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "amount_money": self.amount_money.wire(),
                "tip_money": None if self.tip_money is None else self.tip_money.wire(),
                "total_money": self.total_money.wire(),
                "approved_money": self.approved_money.wire(),
                "status": self.status,
                "source_type": self.source_type,
                "location_id": self.location_id,
                "order_id": self.order_id,
                "reference_id": self.reference_id,
                "customer_id": self.customer_id,
                "note": self.note,
                "external_details": self.external_details,
                "receipt_number": self.receipt_number,
                "receipt_url": self.receipt_url,
                "application_details": dict(self.application_details),
                "version_token": self.version_token,
            }
        )


def version_token_of(payment: PaymentEntity) -> str:
    """The opaque token for one version of one payment.

    A digest rather than the bare version number so that a consumer cannot
    read a counter out of it and start fabricating tokens; 43 characters,
    the length of the documented example.
    """
    return digest_of([payment.id, payment.version])[:43]


def project_payment(payment: PaymentEntity, application_id: str) -> dict[str, Any]:
    """A stored payment as Square's ``Payment`` JSON."""
    currency = payment.amount_money.currency
    completed = payment.status == PaymentState.COMPLETED.value
    approved = 0 if payment.status == PaymentState.CANCELED.value else payment.amount_money.amount
    external: dict[str, Any] | None = None
    if payment.external_type is not None:
        external = compact(
            {
                "type": payment.external_type,
                "source": payment.external_source,
                "source_id": payment.external_source_id,
            }
        )
    return PaymentWire(
        id=payment.id,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        amount_money=money(payment.amount_money.amount, currency),
        tip_money=None if payment.tip_money is None else money(payment.tip_money.amount, currency),
        total_money=money(payment.total, currency),
        approved_money=money(approved, currency),
        status=payment.status,
        source_type=payment.source_type,
        location_id=payment.location_id,
        order_id=payment.order_id,
        reference_id=payment.reference_id,
        customer_id=payment.customer_id,
        note=payment.note,
        external_details=external,
        # "receipt_number: The payment's receipt number. The field is missing
        # if a payment is canceled." -- the first four characters of the id,
        # as the documented example shows (`R2B3` for `R2B3Z8WMVt3E...`).
        receipt_number=payment.id[:4] if completed else None,
        receipt_url=f"https://squareup.com/receipt/preview/{payment.id}" if completed else None,
        application_details={"square_product": SQUARE_PRODUCT, "application_id": application_id},
        version_token=version_token_of(payment),
    ).wire()


# ---------------------------------------------------------------------------
# Requests.
# ---------------------------------------------------------------------------


class ExternalDetailsRequest(BaseModel):
    """``external_details``: "Additional details required for external
    payments". ``type`` and ``source`` are both required on Square's object;
    ``source`` is "A description of the external payment source. For example,
    'Food Delivery Service'" (max 255), ``source_id`` an optional reference
    into that source.
    https://developer.squareup.com/reference/square/objects/ExternalPaymentDetails
    """

    model_config = _REQUEST

    type: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=255)
    source_id: str | None = Field(default=None, max_length=255)


class CreatePaymentRequest(BaseModel):
    """``POST /v2/payments``.
    https://developer.squareup.com/reference/square/payments-api/create-payment

    ``idempotency_key`` is required ("Min Length 1, Max Length 45") and read
    by the kernel through the route's spec. ``autocomplete`` defaults to true:
    "If set to `true`, this payment will be completed when possible. If set
    to `false`, this payment will be held in an approved state until either
    explicitly completed (captured) or canceled (voided)."
    """

    model_config = _REQUEST

    source_id: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, max_length=45)
    amount_money: MoneyRequest
    tip_money: MoneyRequest | None = None
    autocomplete: bool = True
    order_id: str | None = None
    location_id: str | None = None
    customer_id: str | None = None
    reference_id: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=500)
    external_details: ExternalDetailsRequest | None = None


class CompletePaymentRequest(BaseModel):
    """``POST /v2/payments/{payment_id}/complete``.

    ``version_token`` is "Used for optimistic concurrency. This opaque token
    identifies the current `Payment` version that the caller expects. If the
    server has a different version of the Payment, the update fails and a
    response with a VERSION_MISMATCH error is returned."
    https://developer.squareup.com/reference/square/payments-api/complete-payment
    """

    model_config = _REQUEST

    version_token: str | None = None


class CancelPaymentRequest(BaseModel):
    """``POST /v2/payments/{payment_id}/cancel``. Square documents no body
    fields; an empty object is the request.
    https://developer.squareup.com/reference/square/payments-api/cancel-payment
    """

    model_config = _REQUEST
