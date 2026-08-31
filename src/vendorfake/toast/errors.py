"""Toast error shaping -- the entire vendor-side error story, in one table.

FOR: turning each of the core's twenty vendor-neutral error kinds into an HTTP
status and Toast's ``ErrorMessage`` body, so that adding this vendor is a
lookup table rather than error handling scattered through handlers.

INVARIANT: **the table is exhaustive, and every row says where its status came
from.** Exhaustiveness is checked at import and again at unit construction on
``describe()``'s answer. Provenance is a real field because the ``unit_error``
sidecar publishes it and :meth:`ToastErrorShaper.describe` renders the whole
table for ``GET /__unit/errors``. The sidecar, the ``retry-after`` and
``x-unit-capability`` mechanism headers and the exhaustiveness check are the
core's (``core/kernel/shaping.py``); this module is Toast's table, envelope
and the documented rate-limit headers.

The envelope -- DOCUMENTED
--------------------------
https://doc.toasttab.com/doc/devguide/apiResponsesAndErrors.html shows the
body verbatim::

    {"status": 400, "code": 10025, "message": "Payment amount cannot be empty",
     "messageKey": null, "fieldName": null, "link": null,
     "requestId": "2ea769e2-...", "developerMessage": null, "errors": [],
     "canRetry": null}

Every key is emitted on every error, nulls included: this is the one shape in
the package that is *not* compacted, because the page shows the nulls.
``messageKey``, ``fieldName`` and ``canRetry`` are "reserved for future use"
and stay null; ``errors`` is "a nested ErrorMessage list" and stays empty
because nothing here produces a nested failure. The field an error is about
travels in the ``unit_error`` sidecar, not in ``fieldName``.

The ``code`` -- JUDGMENT, with one documented value
---------------------------------------------------
Toast publishes no catalogue of ``code`` values (audit gap 5); ``10025`` for
"Payment amount cannot be empty" is the only one seen. Every row below carries
a code in the same five-digit range, assigned by this project in kind order
from 10001 and never colliding with 10025; a handler that knows the documented
code for its refusal passes it as ``info["toast_code"]`` and the table's is
overridden. A consumer must not build against any code but 10025.

Statuses -- DOCUMENTED where the page lists them
------------------------------------------------
The same page lists 200, 204, 400, 401 (token invalid/expired), 403, 404, 409,
422, 429, 499, 500, 502, 504. ``forbidden_scope`` is **403** -- documented on
``POST /prices`` as "missing scope" -- and distinct from the 401 an invalid
token gets, so a Clover-habituated consumer learns the difference here.
``not_found`` is the documented 404 ("The specified order was not found");
``rate_limited`` is the documented 429 with ``X-Toast-RateLimit-By``,
``X-Toast-RateLimit-Remaining``, ``X-Toast-RateLimit-Reset`` (Unix epoch) and
``Retry-After`` (seconds) from apiRateLimiting.html -- header names documented,
values JUDGMENT because a chaos-injected 429 tripped no real limit.

The ``requestId`` comes from the vendor's request-id stream (``ids.py``),
seeded and salted apart from the entity ids, so a refused request never
renumbers a scenario's guids and two units answer the same sequence.

The ``unit_error`` sidecar is a deliberate, namespaced deviation from Toast's
wire format, off with ``"error_sidecar": false`` in a profile's ``vendor``
block.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from vendorfake.core.kernel.shaping import (
    Provenance,
    assert_error_table_total,
    mechanism_headers,
    unit_error_sidecar,
)
from vendorfake.core.kernel.types import (
    ShapedError,
    UnitContext,
    UnitError,
    UnitErrorKind,
    UnitRequest,
)
from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import as_int
from vendorfake.toast.ids import ToastRequestIds
from vendorfake.toast.model.error import ErrorMessageWire

__all__ = [
    "CODE_PAYMENT_AMOUNT_EMPTY",
    "RATE_LIMIT_BY_HEADER",
    "RATE_LIMIT_REMAINING_HEADER",
    "RATE_LIMIT_RESET_HEADER",
    "RETRY_AFTER_HEADER",
    "TOAST_CODE_INFO_KEY",
    "TOAST_ERROR_TABLE",
    "Provenance",
    "ToastErrorMapping",
    "ToastErrorShaper",
]

CODE_PAYMENT_AMOUNT_EMPTY = 10025
"""The one documented ``code``: "Payment amount cannot be empty"."""

TOAST_CODE_INFO_KEY = "toast_code"
"""``UnitError.info`` key a handler sets to put a specific ``code`` on the wire."""

RATE_LIMIT_BY_HEADER = "X-Toast-RateLimit-By"
RATE_LIMIT_REMAINING_HEADER = "X-Toast-RateLimit-Remaining"
RATE_LIMIT_RESET_HEADER = "X-Toast-RateLimit-Reset"
RETRY_AFTER_HEADER = "Retry-After"
"""The four documented 429 headers (apiRateLimiting.html), in the documented casing."""

_RATE_LIMIT_NOTE = (
    "The 429 status and the X-Toast-RateLimit-By/-Remaining/-Reset and Retry-After headers are documented "
    "(https://doc.toasttab.com/doc/devguide/apiRateLimiting.html); the body and the header values are "
    "judgment, because a chaos-injected 429 tripped no real limit."
)


@dataclass(frozen=True, slots=True)
class ToastErrorMapping:
    """One row: what a core error kind looks like on Toast's wire."""

    status: int
    provenance: Provenance
    #: JUDGMENT scheme except 10025; see the module docstring.
    code: int
    message: str
    note: str | None = None

    def as_json(self) -> dict[str, Any]:
        return compact(
            {
                "status": self.status,
                "provenance": self.provenance,
                "code": self.code,
                "message": self.message,
                "note": self.note,
            }
        )


TOAST_ERROR_TABLE: dict[UnitErrorKind, ToastErrorMapping] = {
    UnitErrorKind.BAD_REQUEST: ToastErrorMapping(400, "documented", 10001, "Bad request."),
    UnitErrorKind.INVALID_JSON: ToastErrorMapping(400, "judgment", 10002, "The request body is not valid JSON."),
    UnitErrorKind.MISSING_FIELD: ToastErrorMapping(400, "judgment", 10003, "A required field is missing."),
    UnitErrorKind.INVALID_VALUE: ToastErrorMapping(400, "judgment", 10004, "The provided value is invalid."),
    UnitErrorKind.NOT_FOUND: ToastErrorMapping(404, "documented", 10005, "The specified entity was not found."),
    UnitErrorKind.METHOD_NOT_ALLOWED: ToastErrorMapping(
        405, "judgment", 10006, "The HTTP method is not allowed on this resource."
    ),
    UnitErrorKind.UNAUTHORIZED: ToastErrorMapping(
        401,
        "documented",
        10007,
        "Unauthorized: the access token is missing, invalid or expired.",
        note="401 is documented as 'token invalid/expired' (apiResponsesAndErrors.html).",
    ),
    UnitErrorKind.TOKEN_EXPIRED: ToastErrorMapping(
        401, "documented", 10008, "Unauthorized: the access token is missing, invalid or expired."
    ),
    UnitErrorKind.TOKEN_REVOKED: ToastErrorMapping(
        401,
        "judgment",
        10009,
        "Unauthorized: the access token is missing, invalid or expired.",
        note="Toast documents no revocation; the kind is mapped to the documented 401 so the table is whole.",
    ),
    UnitErrorKind.FORBIDDEN_SCOPE: ToastErrorMapping(
        403,
        "documented",
        10010,
        "Forbidden: the access token does not carry the scope this endpoint requires.",
        note="403 'missing scope' is documented on POST /orders/v2/prices (toast-orders-api.yaml).",
    ),
    UnitErrorKind.CAPABILITY_DISABLED: ToastErrorMapping(
        501, "judgment", 10011, "This capability is not enabled on this unit."
    ),
    UnitErrorKind.VERSION_CONFLICT: ToastErrorMapping(
        409, "judgment", 10012, "The supplied version does not match the current version."
    ),
    UnitErrorKind.IDEMPOTENCY_CONFLICT: ToastErrorMapping(
        409, "judgment", 10013, "A conflicting request with the same key was already processed."
    ),
    UnitErrorKind.INVALID_CURSOR: ToastErrorMapping(
        400, "judgment", 10014, "The provided pageToken is not valid for this request."
    ),
    UnitErrorKind.INVALID_TRANSITION: ToastErrorMapping(
        400,
        "judgment",
        10015,
        "The entity cannot be updated in its current state.",
        note="'Once an order has been voided, it can not be updated' (apiVoidOrder.html) documents the rule, not the status.",
    ),
    UnitErrorKind.CONFLICT: ToastErrorMapping(409, "documented", 10016, "Conflicting request."),
    UnitErrorKind.RATE_LIMITED: ToastErrorMapping(
        429, "documented", 10017, "Too many requests.", note=_RATE_LIMIT_NOTE
    ),
    UnitErrorKind.TIMEOUT: ToastErrorMapping(504, "documented", 10018, "Gateway timeout."),
    UnitErrorKind.UNAVAILABLE: ToastErrorMapping(503, "documented", 10019, "Service unavailable."),
    UnitErrorKind.INTERNAL: ToastErrorMapping(500, "documented", 10020, "Internal server error."),
}
"""Twenty rows, one per core error kind. Statuses documented where the
responses page lists them; the messages are this project's except where a
handler supplies a documented phrase as its detail."""


class ToastErrorShaper:
    """Turns a :class:`UnitError` into Toast's ErrorMessage. Satisfies ``ErrorShaper``.

    Frozen configuration rather than a live read of the profile, because the
    vendor rebuilds the shaper when its configuration resolves. The request-id
    stream is handed in so the vendor can reseed it at hydrate alongside the
    entity stream.
    """

    __slots__ = ("_request_ids", "_retry_after_header", "_sidecar")

    def __init__(
        self,
        *,
        request_ids: ToastRequestIds,
        sidecar: bool = True,
        retry_after_header: bool = True,
    ) -> None:
        self._request_ids = request_ids
        self._sidecar = sidecar
        self._retry_after_header = retry_after_header

    def shape(self, err: UnitError, ctx: UnitContext) -> ShapedError:
        """One core error, as this unit's Toast would send it.

        ``message`` follows the error's own wording when it has one and the
        table's otherwise -- there is no conflation to protect here, and a
        handler quoting a documented phrase ("The GUID was malformed") is
        exactly what should reach the wire.
        """
        mapping = TOAST_ERROR_TABLE[err.kind]
        info = dict(err.info or {})
        override = info.get(TOAST_CODE_INFO_KEY)
        code = override if isinstance(override, int) and not isinstance(override, bool) else mapping.code
        body: dict[str, Any] = ErrorMessageWire(
            status=mapping.status,
            code=code,
            message=err.detail or mapping.message,
            requestId=self._request_ids.request_id(),
        ).wire()
        if self._sidecar:
            body["unit_error"] = unit_error_sidecar(err, mapping.provenance, field=err.field or None)
        headers = mechanism_headers(err, retry_after_header=self._retry_after_header)
        if "retry-after" in headers:
            # The core's mechanism header, in the casing Toast documents.
            headers[RETRY_AFTER_HEADER] = headers.pop("retry-after")
        if err.kind is UnitErrorKind.RATE_LIMITED:
            retry_after = as_int(info.get("retry_after_seconds"), 1)
            headers[RATE_LIMIT_BY_HEADER] = "ENDPOINT"
            headers[RATE_LIMIT_REMAINING_HEADER] = "0"
            headers[RATE_LIMIT_RESET_HEADER] = str(math.floor(ctx.clock.now() / 1000) + retry_after)
        return ShapedError(status=mapping.status, body=body, headers=headers)

    def not_found(self, req: UnitRequest, ctx: UnitContext) -> ShapedError:
        """The body for a path that matched no route at all; it names the
        control route that lists the surface."""
        return self.shape(
            UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=(
                    f"{req.method} {req.path} is not a route on this Toast unit. "
                    "GET /__unit/routes lists the surface this profile serves."
                ),
                info={"path": req.path, "method": req.method, "profile": ctx.config.profile},
            ),
            ctx,
        )

    def describe(self) -> dict[str, Any]:
        """The table as a report publishes it -- twenty rows with provenance."""
        return {kind.value: mapping.as_json() for kind, mapping in TOAST_ERROR_TABLE.items()}


# Exhaustiveness, at import, as a raise and never as an `assert` -- see
# core/kernel/shaping.py for why.
assert_error_table_total(TOAST_ERROR_TABLE, name="TOAST_ERROR_TABLE")

if CODE_PAYMENT_AMOUNT_EMPTY in {mapping.code for mapping in TOAST_ERROR_TABLE.values()}:
    raise RuntimeError("the judgment code scheme must not reuse the one documented code, 10025")
