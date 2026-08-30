"""Square error shaping -- the entire vendor-side error story, in one table.

FOR: turning each of the core's twenty vendor-neutral error kinds into a
Square ``{category, code}`` pair, an HTTP status and Square's documented error
envelope, so that adding a vendor is a lookup table rather than error handling
scattered through handlers.

INVARIANT: **the table is exhaustive, and every row says where its status came
from.** Exhaustiveness is checked at import (see the bottom of this module),
because the TypeScript original got it from ``Record<UnitErrorKind, Mapping>``
and a compiler; a missing row here would otherwise present as one error kind
answering 500 while the other nineteen behaved. Provenance is a real field and
not a comment because ``/__unit/errors`` and the ``unit_error`` sidecar publish
it: a consumer can ask this fake which of its statuses Square actually
documents and which are this project's reading.

The sidecar, the ``retry-after`` and ``x-unit-capability`` headers and the
exhaustiveness check are the core's (``core/kernel/shaping.py``); this module
is Square's table and Square's envelope.

Envelope and the four ``Error`` fields (``category``, ``code``, ``detail``,
``field``) are documented:
  https://developer.squareup.com/docs/build-basics/handling-errors
  https://developer.squareup.com/reference/square/objects/Error
The verbatim documented example is
``{"errors":[{"category":"AUTHENTICATION_ERROR","code":"UNAUTHORIZED",
"detail":"This request could not be authorized."}]}`` and that is exactly the
shape :meth:`SquareErrorShaper.shape` emits.

Statuses marked ``judgment`` are ones Square does not publish. Square documents
statuses only for the authentication codes and 429
(https://developer.squareup.com/docs/build-basics/handling-errors) plus a
verbatim 400 example for ``IDEMPOTENCY_KEY_REUSED``
(https://developer.squareup.com/docs/build-basics/general-considerations/using-rest-api);
everything else here is the conventional REST reading of the code name, and is
labelled as such rather than presented as fidelity. A public-docs audit of this
table verified the labelling row by row and found it accurate.

Two rows carry a further note, because "judgment" alone understates the
situation:

``version_conflict`` -> ``VERSION_MISMATCH``
    The code is real -- Square names it in prose on the Optimistic Concurrency
    page and a 400 carrying it has been quoted publicly -- but it is **not** in
    the published ``ErrorCode`` enumeration, and its **category is not
    verified** at all. See :data:`UNPUBLISHED_CODES`.

``rate_limited`` -> the ``retry-after`` header
    Square documents 429 and ``RATE_LIMITED``, and prescribes client-side
    exponential backoff with jitter. It documents no ``Retry-After`` header and
    publishes no numeric rate limits. Sending one is a convenience for a
    consumer testing their own backoff, and it is switchable, because a
    consumer who learns to trust it here would busy-loop against the real API.
    JUDGMENT.

The ``unit_error`` sidecar is a deliberate, namespaced deviation from Square's
wire format: a consumer that reads only ``errors`` never sees it, and a
consumer debugging this fake gets the machine-readable reason without parsing
prose. It is off with ``"error_sidecar": false`` in a profile's ``vendor``
block.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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

    The reference's union carries only the four it uses. Carrying all eight
    costs nothing and means a later vendor slice that adds payments does not
    have to widen a type that tests already pin.
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
    """The Square error codes this vendor can emit, plus two it cannot.

    Every member except :data:`UNPUBLISHED_CODES` appears in Square's published
    enumeration (https://developer.squareup.com/reference/square/enums/ErrorCode).
    ``CLIENT_DISABLED`` and ``FORBIDDEN`` are carried although no code path
    reaches them -- see :data:`UNREACHABLE_CODES` -- so that the gap is a named
    fact rather than an omission a reader has to notice.
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
"""Codes Square uses but does not list in the published ``ErrorCode`` enum.

``VERSION_MISMATCH`` is named in prose on
https://developer.squareup.com/docs/working-with-apis/optimistic-concurrency
and has been quoted from a real 400 response in public developer-forum posts,
which is weaker provenance than a documentation page. Its **category** is not
verified anywhere; ``INVALID_REQUEST_ERROR`` below is this project's reading.
"""

UNREACHABLE_CODES: frozenset[ErrorCode] = frozenset({ErrorCode.CLIENT_DISABLED, ErrorCode.FORBIDDEN})
"""Documented codes no core error kind maps onto.

Square publishes 401 ``CLIENT_DISABLED`` and a general 403 ``FORBIDDEN``
(https://developer.squareup.com/docs/build-basics/handling-errors). Neither
corresponds to a state this fake can reach: nothing disables an application,
and every 403 it can produce is a scope failure. Recorded rather than
implemented.
"""


@dataclass(frozen=True, slots=True)
class SquareErrorMapping:
    """One row: what a core error kind looks like on Square's wire."""

    status: int
    category: ErrorCategory
    code: ErrorCode
    #: Where the status comes from; surfaced in the sidecar and by
    #: ``GET /__unit/errors``.
    provenance: Provenance
    detail: str
    #: Set only where "judgment" understates the gap. See the module docstring.
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
    # NOT_IMPLEMENTED is a real Square generic error code (api.json
    # info["x-square-generic-error-codes"]); using it keeps a disabled
    # capability inside the vendor's own vocabulary instead of inventing one.
    # The 501 status and the `unit_error` sidecar are this project's addition.
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
"""Twenty rows, one per core error kind. See the module docstring for
provenance."""

PUBLISHED_ERROR_CODES: frozenset[ErrorCode] = frozenset(ErrorCode) - UNPUBLISHED_CODES
"""Every code above that Square's published ``ErrorCode`` enumeration lists."""


class SquareErrorShaper:
    """Turns a :class:`UnitError` into Square's envelope. Satisfies ``ErrorShaper``.

    Frozen configuration rather than a live read of the profile, because the
    vendor rebuilds the shaper when its configuration resolves; a shaper that
    reached back into a context to ask whether the sidecar was on would have to
    do it on every error.
    """

    __slots__ = ("_retry_after_header", "_sidecar")

    def __init__(self, *, sidecar: bool = True, retry_after_header: bool = True) -> None:
        self._sidecar = sidecar
        self._retry_after_header = retry_after_header

    def shape(self, err: UnitError, ctx: UnitContext) -> ShapedError:
        """One core error, as Square would send it.

        ``detail`` follows the error's own wording when it has one and the
        table's otherwise, so a handler that explains precisely what was wrong
        is not overwritten by a generic sentence. ``field`` is included only
        when the error names one -- Square's ``Error`` object marks it optional
        and a null field pointer would be worse than no key.
        """
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
        if self._sidecar:
            body["unit_error"] = unit_error_sidecar(err, mapping.provenance)
        headers = mechanism_headers(err, retry_after_header=self._retry_after_header)
        return ShapedError(status=mapping.status, body=body, headers=headers)

    def not_found(self, req: UnitRequest, ctx: UnitContext) -> ShapedError:
        """The body for a path that matched no route at all.

        It names the control route that lists the surface, because the most
        common cause of a 404 against a fake is a profile that does not serve
        the capability the caller assumed.
        """
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


# Exhaustiveness, at import, as a raise and never as an `assert` -- see
# core/kernel/shaping.py for why.
assert_error_table_total(SQUARE_ERROR_TABLE, name="SQUARE_ERROR_TABLE")
