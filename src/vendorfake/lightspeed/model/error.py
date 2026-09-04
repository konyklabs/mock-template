"""The error bodies this vendor sends, as strict models.
DOCUMENTED: no vendor-wide error envelope exists (only ``PaymentErrorResponse``
matches ``error|problem``, scoped to payments; the docs site has no
error-codes page). JUDGMENT -- three shapes generalised from what the vendor
prints elsewhere: :class:`ErrorWire` (rate-limiting's 429 body),
:class:`WebhookConflictWire` (``POST /webhooks``'s 409/404 shape) and
:class:`PaymentErrorWire` (``PaymentErrorResponse`` itself, whose
:class:`PaymentErrorCode` table is this project's own)."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "PAYMENT_ERROR_INFO_KEY",
    "ErrorWire",
    "PaymentErrorCode",
    "PaymentErrorWire",
    "WebhookConflictWire",
]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)


class ErrorWire(BaseModel):
    """The generalised two-member body. Key order is the 429 example's."""

    model_config = _WIRE

    error: str
    message: str

    def wire(self) -> dict[str, Any]:
        return {"error": self.error, "message": self.message}


class WebhookConflictWire(BaseModel):
    """The one-member body the Webhooks tag's own 409 and 404 schemas declare."""

    model_config = _WIRE

    error: str

    def wire(self) -> dict[str, Any]:
        return {"error": self.error}


class PaymentErrorWire(BaseModel):
    """``PaymentErrorResponse``, the only named error schema in the document."""

    model_config = _WIRE

    code: int
    message: str

    def wire(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


PAYMENT_ERROR_INFO_KEY = "lightspeed_payment_error_code"
"""``UnitError.info`` key requesting the :class:`PaymentErrorWire` shape."""


class PaymentErrorCode(IntEnum):
    """``PaymentErrorResponse.error.code``. JUDGMENT -- the schema declares only
    ``"type": "integer"``, so these values are this project's own."""

    #: The register the payment names exists but is closed.
    REGISTER_NOT_OPEN = 1001
    #: `SalePayment.type.config_id` names no payment type of this retailer.
    UNKNOWN_PAYMENT_TYPE = 1002
    #: `SalePayment.source.register_id` names no register of this retailer.
    UNKNOWN_REGISTER = 1003
    #: Neither the payment nor the sale named a register to take the money at.
    REGISTER_REQUIRED = 1004
