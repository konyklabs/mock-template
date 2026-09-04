"""Lightspeed error shaping: maps each of the core's error kinds to an HTTP status and body.

INVARIANT: the table is exhaustive, checked at import and again against ``describe()``.

JUDGMENT: Lightspeed publishes no error envelope (see ``model/error.py``); this table
generalises the 429 body documented at
https://x-series-api.lightspeedhq.com/docs/rate_limiting to every refusal, except the
webhooks routes' own one-member ``{"error": "..."}`` shape. DOCUMENTED statuses: 409 on
``POST /webhooks``, 404 on ``/webhooks/{webhookId}``, 429 for rate limiting, 401 globally
via ``bearerAuth``; everything else here is judgment, labelled row by row.

DOCUMENTED: ``Retry-After`` there is an RFC 1123 HTTP-date, not delta-seconds like every
other vendor here -- this shaper replaces the core's delta-seconds value with a formatted
date computed from the unit's clock.
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
"""Present on every response (see ``vendor.decorate`` in ``ratelimit.py``). DOCUMENTED at
https://x-series-api.lightspeedhq.com/docs/rate_limiting; lower-cased here like every header
this project emits, though the page's own prose uses mixed case."""

RETRY_AFTER_HEADER = "retry-after"
"""DOCUMENTED on the 429, as an RFC 1123 HTTP-date rather than delta-seconds."""

RATE_LIMITED_TITLE = "Too Many Requests"
RATE_LIMITED_MESSAGE = "Rate limiting enforced"
"""The 429 body, verbatim from
https://x-series-api.lightspeedhq.com/docs/rate_limiting."""

WEBHOOK_DUPLICATE_MESSAGE = "A webhook with this type and URL already exists."
"""The 409's own ``description`` on ``POST /webhooks``, used as the body's
``error`` value."""

ONE_MEMBER_BODY_INFO_KEY = "lightspeed_one_member_body"
"""``UnitError.info`` key a handler sets to request the webhooks ``{"error": "..."}`` shape
instead of the generalised two-member body -- a property of the refusal, not the route."""

CATALOGUE_RETRY_AFTER = "Thu, 01 Jan 1970 00:00:00 GMT"
"""``Retry-After`` on a *described* 429 (``GET /__unit/errors``), never a real one -- a live
value would drift with the wall clock and break C10's byte-for-byte comparison. The epoch is
not a plausible retry instant, deliberately."""

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
    """RFC 1123 HTTP-date, as the 429 documents it (e.g. ``Wed, 15 Jul 2020 15:04:05 GMT``).

    Written out rather than via :func:`email.utils.formatdate`, which reads the C library's
    locale -- this must not depend on ``LC_TIME``.
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


class LightspeedErrorShaper:
    """Turns a :class:`UnitError` into this vendor's body. Satisfies ``ErrorShaper``.

    Frozen configuration: the vendor rebuilds the shaper when its config resolves.
    """

    __slots__ = ("_retry_after_header", "_sidecar", "_window_ms")

    def __init__(self, *, sidecar: bool = True, retry_after_header: bool = True, window_ms: int = 300_000) -> None:
        self._sidecar = sidecar
        self._retry_after_header = retry_after_header
        self._window_ms = window_ms

    def shape(self, err: UnitError, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """One core error, shaped as this vendor would send it.

        ``message`` follows the error's own wording when set, the table's default otherwise.
        """
        mapping = LIGHTSPEED_ERROR_TABLE[err.kind]
        info = dict(err.info or {})
        detail = err.detail or mapping.message
        body: dict[str, Any]
        payment_code = info.get(PAYMENT_ERROR_INFO_KEY)
        if info.get(ONE_MEMBER_BODY_INFO_KEY) is True:
            body = WebhookConflictWire(error=detail).wire()
        elif isinstance(payment_code, int) and not isinstance(payment_code, bool):
            # Payment refusals use the spec's own PaymentErrorResponse shape; the STATUS is
            # still this table's, since that schema carries none of its own.
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
        # Core's mechanism header is delta-seconds; Lightspeed's is an absolute HTTP-date --
        # replace the value rather than suppress the header.
        headers.pop("retry-after", None)
        if err.kind is UnitErrorKind.RATE_LIMITED and self._retry_after_header:
            headers[RETRY_AFTER_HEADER] = (
                CATALOGUE_RETRY_AFTER
                if describing
                else http_date(ctx.clock.now() + as_int(info.get("retry_after_ms"), self._window_ms))
            )
        return ShapedError(status=mapping.status, body=body, headers=headers)

    def not_found(self, req: UnitRequest, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """The body for a path that matched no route at all; names the control route."""
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


# A raise, not an `assert` -- see core/kernel/shaping.py for why.
assert_error_table_total(LIGHTSPEED_ERROR_TABLE, name="LIGHTSPEED_ERROR_TABLE")
