"""Lightspeed error shaping -- the entire vendor-side error story, in one table.

FOR: turning each of the core's twenty vendor-neutral error kinds into an HTTP
status and a body, so that adding this vendor is a lookup table rather than
error handling scattered through handlers.

INVARIANT: **the table is exhaustive, and every row says where its status came
from.** Exhaustiveness is checked at import and again at unit construction on
``describe()``'s answer.

THE ENVELOPE IS A JUDGMENT CALL, and the loudest one in this package.
Lightspeed publishes no error envelope at all -- see the long note in
``model/error.py``, which records how that absence was verified. This table
generalises the 429 body the rate-limiting page prints verbatim
(``{"error": "Too Many Requests", "message": "Rate limiting enforced"}``) to
every refusal; the webhooks surface keeps the one-member ``{"error": "..."}``
shape its own 409 and 404 schemas declare. Every row below is therefore
``provenance: judgment`` EXCEPT the four the specification's own operations
declare for the routes this slice serves.

WHERE A STATUS IS DOCUMENTED, and where it is not
-------------------------------------------------
Across the whole document 401 appears on 29 operations, 403 on 24, 404 on 39,
409 on 12 and 422 on 10 -- so the *vocabulary* is the vendor's even where a
body is not. For this slice's own surface the specification declares:

* **409** on ``POST /webhooks``, with its message: "A webhook with this type
  and URL already exists."
* **404** on ``GET``/``PUT``/``DELETE /webhooks/{webhookId}``.
* **429** and its body and headers, on the rate-limiting page.
* **401** as the whole document's authentication failure (``bearerAuth`` is
  applied globally at the root and 29 operations declare the status).

Everything else here -- 403 for a missing scope, 422 for a bad field, 400 for
malformed JSON, and every 5xx -- is this project's choice, taken to match the
statuses the document uses elsewhere, and labelled ``judgment`` row by row.

``Retry-After`` IS AN HTTP-DATE, NOT SECONDS. The rate-limiting page prints
``Retry-After: Wed, 15 Jul 2020 15:04:05 GMT`` -- an absolute instant in RFC
1123 form. The core's ``mechanism_headers`` emits delta-seconds, which is what
every other vendor here documents, so this shaper replaces the value with the
formatted date computed from the unit's clock. A consumer's retry code that
parses an integer fails here, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vendorfake.core.kernel.shaping import (
    Provenance,
    assert_error_table_total,
    mechanism_headers,
    sidecar_headers,
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
from vendorfake.lightspeed.model.error import (
    PAYMENT_ERROR_INFO_KEY,
    ErrorWire,
    PaymentErrorWire,
    WebhookConflictWire,
)

__all__ = [
    "CATALOGUE_RETRY_AFTER",
    "LIGHTSPEED_ERROR_TABLE",
    "ONE_MEMBER_BODY_INFO_KEY",
    "PAYMENT_ERROR_INFO_KEY",
    "RATE_LIMITED_MESSAGE",
    "RATE_LIMITED_TITLE",
    "RATE_LIMIT_LIMIT_HEADER",
    "RATE_LIMIT_REMAINING_HEADER",
    "RETRY_AFTER_HEADER",
    "WEBHOOK_DUPLICATE_MESSAGE",
    "LightspeedErrorMapping",
    "LightspeedErrorShaper",
    "Provenance",
    "http_date",
]

RATE_LIMIT_LIMIT_HEADER = "x-ratelimit-limit"
RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-remaining"
"""The two headers the rate-limiting page says are present on EVERY response.
They are stamped by ``vendor.decorate`` for every answer, not only for a 429;
see ``ratelimit.py``.

**Lower-cased, deliberately, and it is not a deviation.** HTTP header names are
case-insensitive, the rate-limiting page's own example prints them lower-cased
(``x-ratelimit-limit: 100``) even where its prose spells them
``X-RateLimit-Limit``, and every header this project emits is lower-cased at
the one place a response is built
(:func:`vendorfake.core.kernel.reply.normalize`). ``decorate`` writes into the
response AFTER that point, so a mixed-case name here would be the only header
on the wire that kept its casing -- and conformance C10 compares the two
bindings' header names, where an HTTP client that normalises and an in-process
one that does not would then disagree."""

RETRY_AFTER_HEADER = "retry-after"
"""DOCUMENTED on the 429, and documented as an RFC 1123 HTTP-date rather than
delta-seconds. Lower-cased for the reason above."""

RATE_LIMITED_TITLE = "Too Many Requests"
RATE_LIMITED_MESSAGE = "Rate limiting enforced"
"""The 429 body, verbatim from
https://x-series-api.lightspeedhq.com/docs/rate_limiting."""

WEBHOOK_DUPLICATE_MESSAGE = "A webhook with this type and URL already exists."
"""The 409's own ``description`` on ``POST /webhooks``, used as the body's
``error`` value."""

ONE_MEMBER_BODY_INFO_KEY = "lightspeed_one_member_body"
"""``UnitError.info`` key a handler sets to ask for the ``{"error": "..."}``
shape the Webhooks tag declares, instead of the generalised two-member body.
An info key rather than a second shaper method because it is a property of the
*refusal* -- which operation raised it -- and the shaper has the error, not the
route."""

CATALOGUE_RETRY_AFTER = "Thu, 01 Jan 1970 00:00:00 GMT"
"""``Retry-After`` on a *described* 429, never on a real one.

``GET /__unit/errors`` renders every kind as a description of this table. A
live value is ``now + retry_after``, so the catalogue would move whenever a
read crossed a wall-clock second and conformance C10 -- which compares the
two bindings byte for byte on exactly this route -- could never pass. The
epoch is not a plausible retry instant, which is the point: a description says
the header exists and what shape it has, not when a window that never opened
would close."""

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_RATE_LIMIT_NOTE = (
    "The 429 status, the body, the X-RateLimit-Limit/-Remaining headers and the RFC 1123 Retry-After are all "
    "documented (https://x-series-api.lightspeedhq.com/docs/rate_limiting); the retry instant is computed from "
    "this unit's clock and the documented 5-minute window."
)

_ENVELOPE_NOTE = (
    "Lightspeed publishes no error envelope: one error schema exists in the whole specification "
    "(PaymentErrorResponse, scoped to payments) and the documentation site has no error-codes page. This body "
    "generalises the one the rate-limiting page prints. JUDGMENT -- see model/error.py."
)


def http_date(epoch_ms: float) -> str:
    """``Wed, 15 Jul 2020 15:04:05 GMT`` -- RFC 1123, as the 429 documents it.

    Written out rather than taken from :func:`email.utils.formatdate`, which
    reads the C library's locale for the day and month abbreviations: a
    consumer's byte-for-byte comparison of two bindings must not depend on
    ``LC_TIME``, and neither must this fake's own transcript.
    """
    import time as _time

    parts = _time.gmtime(epoch_ms / 1000.0)
    return (
        f"{_DAYS[parts.tm_wday]}, {parts.tm_mday:02d} {_MONTHS[parts.tm_mon - 1]} {parts.tm_year:04d} "
        f"{parts.tm_hour:02d}:{parts.tm_min:02d}:{parts.tm_sec:02d} GMT"
    )


@dataclass(frozen=True, slots=True)
class LightspeedErrorMapping:
    """One row: what a core error kind looks like on this vendor's wire."""

    status: int
    provenance: Provenance
    #: The ``error`` member: the status's reason phrase, as a title.
    title: str
    #: The ``message`` member, when the error carries no detail of its own.
    message: str
    note: str | None = None

    def as_json(self) -> dict[str, Any]:
        return compact(
            {
                "status": self.status,
                "provenance": self.provenance,
                "title": self.title,
                "message": self.message,
                "note": self.note,
            }
        )


LIGHTSPEED_ERROR_TABLE: dict[UnitErrorKind, LightspeedErrorMapping] = {
    UnitErrorKind.BAD_REQUEST: LightspeedErrorMapping(
        400, "judgment", "Bad Request", "The request could not be understood.", note=_ENVELOPE_NOTE
    ),
    UnitErrorKind.INVALID_JSON: LightspeedErrorMapping(
        400, "judgment", "Bad Request", "The request body is not valid JSON.", note=_ENVELOPE_NOTE
    ),
    UnitErrorKind.MISSING_FIELD: LightspeedErrorMapping(
        422,
        "judgment",
        "Unprocessable Entity",
        "A required field is missing.",
        note="422 is declared on ten operations in the specification; which field is named is this project's.",
    ),
    UnitErrorKind.INVALID_VALUE: LightspeedErrorMapping(
        422,
        "judgment",
        "Unprocessable Entity",
        "The provided value is not valid.",
        note="422 is declared on ten operations in the specification; the body is this project's.",
    ),
    UnitErrorKind.NOT_FOUND: LightspeedErrorMapping(
        404,
        "documented",
        "Not Found",
        "The requested resource was not found.",
        note="404 is declared on 39 operations, this slice's three /webhooks/{webhookId} routes included.",
    ),
    UnitErrorKind.METHOD_NOT_ALLOWED: LightspeedErrorMapping(
        405, "judgment", "Method Not Allowed", "The HTTP method is not allowed on this resource."
    ),
    UnitErrorKind.UNAUTHORIZED: LightspeedErrorMapping(
        401,
        "documented",
        "Unauthorized",
        "The access token is missing, invalid, expired or revoked.",
        note="bearerAuth is applied at the document root and 401 is declared on 29 operations.",
    ),
    UnitErrorKind.TOKEN_EXPIRED: LightspeedErrorMapping(
        401, "documented", "Unauthorized", "The access token has expired; refresh it or authorize again."
    ),
    UnitErrorKind.TOKEN_REVOKED: LightspeedErrorMapping(
        401,
        "judgment",
        "Unauthorized",
        "The access token was revoked.",
        note=(
            "'Using a refresh token will revoke the access token that was returned with it' "
            "(https://x-series-api.lightspeedhq.com/docs/authorization) documents the revocation; the status a "
            "revoked token then gets is this project's, mapped onto the documented 401 so the table is whole."
        ),
    ),
    UnitErrorKind.FORBIDDEN_SCOPE: LightspeedErrorMapping(
        403,
        "judgment",
        "Forbidden",
        "The access token does not carry the scope this endpoint requires.",
        note=(
            "403 is declared on 24 operations and every operation names its required scope in its description; "
            "no page states that a missing scope is what produces the 403, so the connection is this project's."
        ),
    ),
    UnitErrorKind.CAPABILITY_DISABLED: LightspeedErrorMapping(
        501, "judgment", "Not Implemented", "This capability is not enabled on this unit."
    ),
    UnitErrorKind.VERSION_CONFLICT: LightspeedErrorMapping(
        409, "judgment", "Conflict", "The supplied version does not match the current version."
    ),
    UnitErrorKind.IDEMPOTENCY_CONFLICT: LightspeedErrorMapping(
        409, "judgment", "Conflict", "A conflicting request with the same key was already processed."
    ),
    UnitErrorKind.INVALID_CURSOR: LightspeedErrorMapping(
        422,
        "judgment",
        "Unprocessable Entity",
        "The version cursor is not valid for this request.",
        note="Lightspeed's cursor is a plain version integer, so a malformed one is a bad field value.",
    ),
    UnitErrorKind.INVALID_TRANSITION: LightspeedErrorMapping(
        409,
        "judgment",
        "Conflict",
        "The entity cannot be changed in its current state.",
        note="Opening an open register, or closing a closed one; no page states the status.",
    ),
    UnitErrorKind.CONFLICT: LightspeedErrorMapping(
        409,
        "documented",
        "Conflict",
        "Conflicting request.",
        note="409 is declared on 12 operations, POST /webhooks among them.",
    ),
    UnitErrorKind.RATE_LIMITED: LightspeedErrorMapping(
        429, "documented", RATE_LIMITED_TITLE, RATE_LIMITED_MESSAGE, note=_RATE_LIMIT_NOTE
    ),
    UnitErrorKind.TIMEOUT: LightspeedErrorMapping(504, "judgment", "Gateway Timeout", "Gateway timeout."),
    UnitErrorKind.UNAVAILABLE: LightspeedErrorMapping(503, "judgment", "Service Unavailable", "Service unavailable."),
    UnitErrorKind.INTERNAL: LightspeedErrorMapping(500, "judgment", "Internal Server Error", "Internal server error."),
}
"""Twenty rows, one per core error kind."""


class LightspeedErrorShaper:
    """Turns a :class:`UnitError` into this vendor's body. Satisfies ``ErrorShaper``.

    Frozen configuration rather than a live read of the profile, because the
    vendor rebuilds the shaper when its configuration resolves.
    """

    __slots__ = ("_retry_after_header", "_sidecar", "_window_ms")

    def __init__(self, *, sidecar: bool = True, retry_after_header: bool = True, window_ms: int = 300_000) -> None:
        self._sidecar = sidecar
        self._retry_after_header = retry_after_header
        self._window_ms = window_ms

    def shape(self, err: UnitError, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """One core error, as this unit's Lightspeed would send it.

        ``message`` follows the error's own wording when it has one and the
        table's otherwise: a handler quoting the vendor's own phrase ("A
        webhook with this type and URL already exists") is exactly what should
        reach the wire.
        """
        mapping = LIGHTSPEED_ERROR_TABLE[err.kind]
        info = dict(err.info or {})
        detail = err.detail or mapping.message
        body: dict[str, Any]
        payment_code = info.get(PAYMENT_ERROR_INFO_KEY)
        if info.get(ONE_MEMBER_BODY_INFO_KEY) is True:
            body = WebhookConflictWire(error=detail).wire()
        elif isinstance(payment_code, int) and not isinstance(payment_code, bool):
            # The Sales surface's payment refusals, in the one error shape the
            # specification actually names (``PaymentErrorResponse``). The
            # STATUS is still this table's -- a closed register is the 409 an
            # invalid transition gets, an unresolvable id the 422 a bad value
            # gets -- because the schema carries no status of its own.
            body = PaymentErrorWire(code=payment_code, message=detail).wire()
        else:
            body = ErrorWire(error=mapping.title, message=detail).wire()
        headers = mechanism_headers(err, retry_after_header=self._retry_after_header)
        if self._sidecar:
            sidecar = unit_error_sidecar(err, mapping.provenance, field=err.field or None)
            mode = ctx.config.errors.sidecar
            if mode != "headers":
                body["unit_error"] = sidecar
            if mode != "body":
                headers.update(sidecar_headers(sidecar))
        # The core's mechanism header is delta-seconds; Lightspeed's is an
        # absolute HTTP-date. Replace the value rather than suppress the
        # header, so the switch still means "send Retry-After or do not".
        headers.pop("retry-after", None)
        if err.kind is UnitErrorKind.RATE_LIMITED and self._retry_after_header:
            headers[RETRY_AFTER_HEADER] = (
                CATALOGUE_RETRY_AFTER
                if describing
                else http_date(ctx.clock.now() + as_int(info.get("retry_after_ms"), self._window_ms))
            )
        return ShapedError(status=mapping.status, body=body, headers=headers)

    def not_found(self, req: UnitRequest, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """The body for a path that matched no route at all; it names the
        control route that lists the surface."""
        return self.shape(
            UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=(
                    f"{req.method} {req.path} is not a route on this Lightspeed unit. "
                    "GET /__unit/routes lists the surface this profile serves."
                ),
                info={"path": req.path, "method": req.method, "profile": ctx.config.profile},
            ),
            ctx,
            describing=describing,
        )

    def describe(self) -> dict[str, Any]:
        """The table as a report publishes it -- twenty rows with provenance."""
        return {kind.value: mapping.as_json() for kind, mapping in LIGHTSPEED_ERROR_TABLE.items()}


# Exhaustiveness, at import, as a raise and never as an `assert` -- see
# core/kernel/shaping.py for why.
assert_error_table_total(LIGHTSPEED_ERROR_TABLE, name="LIGHTSPEED_ERROR_TABLE")
