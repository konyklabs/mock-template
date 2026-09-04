"""Square error shaping: each of the core's twenty vendor-neutral error kinds maps to a Square
``{category, code}`` pair, an HTTP status, and Square's documented envelope.
https://developer.squareup.com/docs/build-basics/handling-errors https://developer.squareup.com/reference/square/objects/Error

INVARIANT: the table is exhaustive, checked at import. JUDGMENT rows are the conventional REST reading of
the code name where Square publishes no status. ``version_conflict`` -> ``VERSION_MISMATCH`` is JUDGMENT,
NOT VERIFIED: named in prose on Square's Optimistic Concurrency page but absent from the published
``ErrorCode`` enum (:data:`UNPUBLISHED_CODES`). ``rate_limited`` is JUDGMENT: Square documents
429/``RATE_LIMITED`` but no ``Retry-After`` header or numeric limits.
The ``unit_error`` sidecar is this project's own addition, off via ``errors.sidecar``; by default the
``errors`` array alone is byte-for-byte what Square would send.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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

__all__ = [
    "PUBLISHED_ERROR_CODES",
    "SQUARE_ERROR_TABLE",
    "UNPUBLISHED_CODES",
    "UNREACHABLE_CODES",
    "ErrorCategory",
    "ErrorCode",
    "Provenance",
    "SquareErrorMapping",
    "SquareErrorShaper",
]


class ErrorCategory(StrEnum):
    """All eight documented categories.
    https://developer.squareup.com/reference/square/enums/ErrorCategory
    """

    API_ERROR = "API_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    INVALID_REQUEST_ERROR = "INVALID_REQUEST_ERROR"
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"
    PAYMENT_METHOD_ERROR = "PAYMENT_METHOD_ERROR"
    REFUND_ERROR = "REFUND_ERROR"
    MERCHANT_SUBSCRIPTION_ERROR = "MERCHANT_SUBSCRIPTION_ERROR"
    EXTERNAL_VENDOR_ERROR = "EXTERNAL_VENDOR_ERROR"


class ErrorCode(StrEnum):
    """The Square error codes this vendor can emit, plus two it cannot reach.
    https://developer.squareup.com/reference/square/enums/ErrorCode
    See :data:`UNPUBLISHED_CODES` and :data:`UNREACHABLE_CODES`.
    """

    BAD_REQUEST = "BAD_REQUEST"
    EXPECTED_JSON_BODY = "EXPECTED_JSON_BODY"
    MISSING_REQUIRED_PARAMETER = "MISSING_REQUIRED_PARAMETER"
    INVALID_VALUE = "INVALID_VALUE"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    UNAUTHORIZED = "UNAUTHORIZED"
    ACCESS_TOKEN_EXPIRED = "ACCESS_TOKEN_EXPIRED"
    ACCESS_TOKEN_REVOKED = "ACCESS_TOKEN_REVOKED"
    CLIENT_DISABLED = "CLIENT_DISABLED"
    INSUFFICIENT_SCOPES = "INSUFFICIENT_SCOPES"
    FORBIDDEN = "FORBIDDEN"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    INVALID_CURSOR = "INVALID_CURSOR"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


UNPUBLISHED_CODES: frozenset[ErrorCode] = frozenset({ErrorCode.VERSION_MISMATCH})
"""Codes Square uses but does not list in the published ``ErrorCode`` enum. NOT VERIFIED:
``VERSION_MISMATCH`` is named only in prose
(https://developer.squareup.com/docs/working-with-apis/optimistic-concurrency)
and developer-forum posts, not a docs page."""

UNREACHABLE_CODES: frozenset[ErrorCode] = frozenset({ErrorCode.CLIENT_DISABLED, ErrorCode.FORBIDDEN})
"""Documented codes no core error kind maps onto: 401 ``CLIENT_DISABLED`` and a general 403
``FORBIDDEN``. Recorded rather than implemented, since nothing here reaches either."""


@dataclass(frozen=True, slots=True)
class SquareErrorMapping:
    """One row: what a core error kind looks like on Square's wire."""

    status: int
    category: ErrorCategory
    code: ErrorCode
    #: Where the status comes from; surfaced in the sidecar and by ``GET /__unit/errors``.
    provenance: Provenance
    detail: str
    #: Set only where "judgment" understates the gap.
    note: str | None = None

    def as_json(self) -> dict[str, Any]:
        """The row as a report publishes it."""
        return compact(
            {
                "status": self.status,
                "category": self.category.value,
                "code": self.code.value,
                "provenance": self.provenance,
                "detail": self.detail,
                "note": self.note,
            }
        )


_VERSION_MISMATCH_NOTE = (
    "VERSION_MISMATCH is named in prose on Square's Optimistic Concurrency page but is absent from the "
    "published ErrorCode enum, and its category is NOT VERIFIED by any Square document."
)

SQUARE_ERROR_TABLE: dict[UnitErrorKind, SquareErrorMapping] = {
    UnitErrorKind.BAD_REQUEST: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.BAD_REQUEST,
        provenance="judgment",
        detail="A general error occurred with the request.",
    ),
    UnitErrorKind.INVALID_JSON: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.EXPECTED_JSON_BODY,
        provenance="judgment",
        detail="The request body is not valid JSON.",
    ),
    UnitErrorKind.MISSING_FIELD: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.MISSING_REQUIRED_PARAMETER,
        provenance="judgment",
        detail="A required parameter is missing.",
    ),
    UnitErrorKind.INVALID_VALUE: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.INVALID_VALUE,
        provenance="judgment",
        detail="The provided value is invalid.",
    ),
    UnitErrorKind.NOT_FOUND: SquareErrorMapping(
        status=404,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.NOT_FOUND,
        provenance="judgment",
        detail="Not Found - a general error occurred.",
    ),
    UnitErrorKind.METHOD_NOT_ALLOWED: SquareErrorMapping(
        status=405,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.METHOD_NOT_ALLOWED,
        provenance="judgment",
        detail="The HTTP method is not allowed on this resource.",
    ),
    UnitErrorKind.UNAUTHORIZED: SquareErrorMapping(
        status=401,
        category=ErrorCategory.AUTHENTICATION_ERROR,
        code=ErrorCode.UNAUTHORIZED,
        provenance="documented",
        detail="This request could not be authorized.",
    ),
    UnitErrorKind.TOKEN_EXPIRED: SquareErrorMapping(
        status=401,
        category=ErrorCategory.AUTHENTICATION_ERROR,
        code=ErrorCode.ACCESS_TOKEN_EXPIRED,
        provenance="documented",
        detail="The provided access token has expired.",
    ),
    UnitErrorKind.TOKEN_REVOKED: SquareErrorMapping(
        status=401,
        category=ErrorCategory.AUTHENTICATION_ERROR,
        code=ErrorCode.ACCESS_TOKEN_REVOKED,
        provenance="documented",
        detail="The provided access token has been revoked.",
    ),
    UnitErrorKind.FORBIDDEN_SCOPE: SquareErrorMapping(
        status=403,
        category=ErrorCategory.AUTHENTICATION_ERROR,
        code=ErrorCode.INSUFFICIENT_SCOPES,
        provenance="documented",
        detail="The provided access token does not have permission to execute the requested action.",
    ),
    # NOT_IMPLEMENTED is a real Square generic error code; the 501 status is this project's addition.
    UnitErrorKind.CAPABILITY_DISABLED: SquareErrorMapping(
        status=501,
        category=ErrorCategory.API_ERROR,
        code=ErrorCode.NOT_IMPLEMENTED,
        provenance="judgment",
        detail="This capability is not enabled on this unit.",
    ),
    UnitErrorKind.VERSION_CONFLICT: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.VERSION_MISMATCH,
        provenance="judgment",
        detail="The supplied version does not match the current version.",
        note=_VERSION_MISMATCH_NOTE,
    ),
    # DOCUMENTED: Square's verbatim 400 example for IDEMPOTENCY_KEY_REUSED, at
    # https://developer.squareup.com/docs/build-basics/general-considerations/using-rest-api
    UnitErrorKind.IDEMPOTENCY_CONFLICT: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.IDEMPOTENCY_KEY_REUSED,
        provenance="documented",
        detail="The idempotency key can only be retried with the same request data.",
    ),
    UnitErrorKind.INVALID_CURSOR: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.INVALID_CURSOR,
        provenance="judgment",
        detail="The provided cursor is not valid.",
    ),
    UnitErrorKind.INVALID_TRANSITION: SquareErrorMapping(
        status=400,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.BAD_REQUEST,
        provenance="judgment",
        detail="The order cannot be updated in its current state.",
    ),
    UnitErrorKind.CONFLICT: SquareErrorMapping(
        status=409,
        category=ErrorCategory.INVALID_REQUEST_ERROR,
        code=ErrorCode.CONFLICT,
        provenance="judgment",
        detail="Conflict - a general error occurred.",
    ),
    UnitErrorKind.RATE_LIMITED: SquareErrorMapping(
        status=429,
        category=ErrorCategory.RATE_LIMIT_ERROR,
        code=ErrorCode.RATE_LIMITED,
        provenance="documented",
        detail="Rate Limited - a general error occurred.",
    ),
    UnitErrorKind.TIMEOUT: SquareErrorMapping(
        status=504,
        category=ErrorCategory.API_ERROR,
        code=ErrorCode.GATEWAY_TIMEOUT,
        provenance="judgment",
        detail="Gateway Timeout - a general error occurred.",
    ),
    UnitErrorKind.UNAVAILABLE: SquareErrorMapping(
        status=503,
        category=ErrorCategory.API_ERROR,
        code=ErrorCode.SERVICE_UNAVAILABLE,
        provenance="judgment",
        detail="Service Unavailable - a general error occurred.",
    ),
    UnitErrorKind.INTERNAL: SquareErrorMapping(
        status=500,
        category=ErrorCategory.API_ERROR,
        code=ErrorCode.INTERNAL_SERVER_ERROR,
        provenance="judgment",
        detail="A general server error occurred.",
    ),
}
"""Twenty rows, one per core error kind."""

PUBLISHED_ERROR_CODES: frozenset[ErrorCode] = frozenset(ErrorCode) - UNPUBLISHED_CODES
"""Every code above that Square's published ``ErrorCode`` enumeration lists."""


class SquareErrorShaper:
    """Turns a :class:`UnitError` into Square's envelope. Satisfies ``ErrorShaper``; the vendor rebuilds
    it when configuration resolves."""

    __slots__ = ("_retry_after_header", "_sidecar")

    def __init__(self, *, sidecar: bool = True, retry_after_header: bool = True) -> None:
        self._sidecar = sidecar
        self._retry_after_header = retry_after_header

    def shape(self, err: UnitError, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """One core error, as Square would send it. ``describing`` is ignored (a described body and a real
        one are the same bytes); ``detail`` follows the error's own wording when present, the table's
        otherwise."""
        mapping = SQUARE_ERROR_TABLE[err.kind]
        detail = err.detail if err.detail else mapping.detail
        body: dict[str, Any] = {
            "errors": [
                compact(
                    {
                        "category": mapping.category.value,
                        "code": mapping.code.value,
                        "detail": detail or None,
                        "field": err.field or None,
                    }
                )
            ]
        }
        headers = mechanism_headers(err, retry_after_header=self._retry_after_header)
        if self._sidecar:
            # Sidecar shape and key order: roadmap#35. Placement (body vs headers): roadmap#71.
            sidecar = unit_error_sidecar(err, mapping.provenance)
            mode = ctx.config.errors.sidecar
            if mode != "headers":
                body["unit_error"] = sidecar
            if mode != "body":
                headers.update(sidecar_headers(sidecar))
        return ShapedError(status=mapping.status, body=body, headers=headers)

    def not_found(self, req: UnitRequest, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """The body for a path that matched no route at all; names the control route that lists the surface."""
        return self.shape(
            UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=(
                    f"{req.method} {req.path} is not a route on this Square unit. "
                    "GET /__unit/routes lists the surface this profile serves."
                ),
                info={"path": req.path, "method": req.method, "profile": ctx.config.profile},
            ),
            ctx,
        )

    def describe(self) -> dict[str, dict[str, Any]]:
        """The table as a report publishes it -- twenty rows with provenance."""
        return {kind.value: mapping.as_json() for kind, mapping in SQUARE_ERROR_TABLE.items()}


# Exhaustiveness at import, as a raise rather than `assert`.
assert_error_table_total(SQUARE_ERROR_TABLE, name="SQUARE_ERROR_TABLE")
