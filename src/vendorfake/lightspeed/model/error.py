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
    Declared, named and required exactly so in ``PaymentErrorResponse``. No
    route in this slice returns it; it is modelled here because it is the only
    error schema the vendor names, and the payments surface a later slice adds
    must not reinvent it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["ErrorWire", "PaymentErrorWire", "WebhookConflictWire"]

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
