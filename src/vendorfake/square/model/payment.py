"""The payment wire vocabulary: what CreatePayment accepts, and the ``Payment`` JSON that goes back
out. https://developer.squareup.com/reference/square/objects/Payment
``receipt_number``/``receipt_url`` appear only on a COMPLETED payment; ``approved_money`` is zero on
CANCELED, JUDGMENT from "The amount of money approved for this payment". Card-hold, fee, refund and
address fields are not modeled -- SHRINK (prototype).
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
"""``source_id`` for a payment taken outside Square; the only source this unit accepts."""

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
"""``ExternalPaymentDetails.type``'s documented values.
https://developer.squareup.com/reference/square/objects/ExternalPaymentDetails"""

SQUARE_PRODUCT = "ECOMMERCE_API"
"""``application_details.square_product`` for a payment taken through the API.
https://developer.squareup.com/reference/square/enums/ApplicationDetailsExternalSquareProduct"""

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
    """Opaque per-version token: a digest, not the bare version number, so a consumer can't
    fabricate one; 43 characters, the documented example's length."""
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
        receipt_number=payment.id[:4] if completed else None,
        receipt_url=f"https://squareup.com/receipt/preview/{payment.id}" if completed else None,
        application_details={"square_product": SQUARE_PRODUCT, "application_id": application_id},
        version_token=version_token_of(payment),
    ).wire()


# ---------------------------------------------------------------------------
# Requests.
# ---------------------------------------------------------------------------


class ExternalDetailsRequest(BaseModel):
    """``external_details``: ``type`` and ``source`` are both required; ``source`` is a free-text
    description (max 255) of the payment source, ``source_id`` an optional reference into it."""

    model_config = _REQUEST

    type: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=255)
    source_id: str | None = Field(default=None, max_length=255)


class CreatePaymentRequest(BaseModel):
    """``POST /v2/payments``.
    https://developer.squareup.com/reference/square/payments-api/create-payment

    ``idempotency_key`` is read by the kernel through the route spec.
    ``autocomplete`` defaults true; false holds the payment APPROVED until a separate call."""

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
    """``POST /v2/payments/{payment_id}/complete``. A stale ``version_token`` fails with the
    documented VERSION_MISMATCH.
    https://developer.squareup.com/reference/square/payments-api/complete-payment
    """

    model_config = _REQUEST

    version_token: str | None = None


class CancelPaymentRequest(BaseModel):
    """``POST /v2/payments/{payment_id}/cancel``; Square documents no body fields.
    https://developer.squareup.com/reference/square/payments-api/cancel-payment
    """

    model_config = _REQUEST
