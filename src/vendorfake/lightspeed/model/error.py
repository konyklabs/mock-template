"""The error bodies this vendor sends, as strict models.

THERE IS NO VENDOR-WIDE ERROR ENVELOPE, and that is a verified absence rather
than a gap in the research. Of the specification's 373 component schemas
exactly one matches ``error|problem`` -- ``PaymentErrorResponse`` -- and it is
scoped to payment operations. Most operations that declare a 4xx give a bare
``description`` string with no ``content`` at all (``{"description": "Bad
Request"}``); where a body IS given it is ad hoc and inline. The documentation
site has no error-codes page: its own ``llms.txt`` index says "No dedicated
error codes page listed" under "Error Handling & Codes", and ``/docs/errors``
and ``/docs/error_handling`` answer 404.

So this package picks two shapes and generalises them. Both are JUDGMENT, and
both are shapes the vendor really prints somewhere:

:class:`ErrorWire` -- ``{"error": "<Title>", "message": "<detail>"}``
    The 429 body the rate-limiting page prints verbatim,
    ``{"error": "Too Many Requests", "message": "Rate limiting enforced"}``,
    generalised to every 4xx and 5xx. ``error`` is the status's reason phrase
    and ``message`` the detail.

:class:`WebhookConflictWire` -- ``{"error": "<detail>"}``
    ``POST /webhooks``' 409 declares an inline schema with ``error`` as a
    plain string and no second member ("A webhook with this type and URL
    already exists"), and the three ``/webhooks/{webhookId}`` 404s declare the
    same one-member shape. Emitting the two-member body there would contradict
    the one place the vendor does declare a schema, so the webhooks surface
    keeps this one.

:class:`PaymentErrorWire` -- ``{"error": {"code": int, "message": str}}``
    Declared, named and required exactly so in ``PaymentErrorResponse``. It is
    the only error schema the vendor names anywhere. The Sales surface answers
    it for the refusals that are about a payment rather than about the sale's
    own fields; :class:`PaymentErrorCode` is the code table, and every value in
    it is this project's, because the vendor publishes none.
"""

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
"""``UnitError.info`` key a handler sets to ask for the
:class:`PaymentErrorWire` shape instead of the generalised two-member body,
carrying the integer code the schema requires.

An info key rather than a second shaper method, for the same reason
``ONE_MEMBER_BODY_INFO_KEY`` in ``errors.py`` is one: the body shape is a
property of the *refusal* -- which operation raised it, and about what -- and
the shaper is handed the error, not the route.
"""


class PaymentErrorCode(IntEnum):
    """``PaymentErrorResponse.error.code``. **Every value is JUDGMENT.**

    ``PaymentErrorResponse`` declares ``code`` as ``"type": "integer"`` and
    nothing else -- no enum, no example value, no range. The documentation site
    has no error-codes page at all (its own ``llms.txt`` index says "No
    dedicated error codes page listed" under "Error Handling & Codes", and
    ``/docs/errors`` and ``/docs/error_handling`` answer 404), so there is no
    published code to reproduce and no way to infer one.

    These are therefore this project's, chosen to be obviously synthetic: a
    four-digit block starting at 1001, dense and contiguous, which no real
    vendor's sparse historical numbering would look like. A consumer must not
    hard-code one of these expecting the real API to send it -- which is
    precisely why they are grouped here, in one table, under this docstring,
    rather than written as literals at five call sites.
    """

    #: The register the payment names exists but is closed. `register:open` is
    #: documented as "Open a register to create sales and payments", which is
    #: the closest the vendor comes to stating this rule.
    REGISTER_NOT_OPEN = 1001
    #: `SalePayment.type.config_id` names no payment type of this retailer.
    UNKNOWN_PAYMENT_TYPE = 1002
    #: `SalePayment.source.register_id` names no register of this retailer.
    UNKNOWN_REGISTER = 1003
    #: Neither the payment nor the sale named a register, so there is no till
    #: to take the money at.
    REGISTER_REQUIRED = 1004
