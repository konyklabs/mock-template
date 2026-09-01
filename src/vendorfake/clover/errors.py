"""Clover error shaping -- the entire vendor-side error story, in one table.

FOR: turning each of the core's twenty vendor-neutral error kinds into an HTTP
status and Clover's error envelope, so that adding this vendor is a lookup
table rather than error handling scattered through handlers.

INVARIANT: **the table is exhaustive, and every row says where its status came
from.** Exhaustiveness is checked at import (see the bottom of this module); a
missing row would otherwise present as one error kind answering 500 while the
other nineteen behaved. Provenance is a real field because the ``unit_error``
sidecar publishes it (when the sidecar is on): a consumer debugging a refusal
sees which of this unit's statuses Clover actually documents and which are
this project's reading, and :meth:`CloverErrorShaper.describe` renders the
whole table for ``GET /__unit/errors``. The sidecar, the ``retry-after`` and
``x-unit-capability`` headers and the exhaustiveness check are the core's
(``core/kernel/shaping.py``); this module is Clover's table and envelope.

The envelope -- JUDGMENT
------------------------
``{"message": "..."}``, plus ``"type"`` where a documented value exists. The
only official example of a Clover error body anywhere is on the status-code
reference, https://docs.clover.com/dev/docs/status-code-and-error-reference,
which shows ``{"message": "...", "type": "RESOURCE_CONFLICT"}`` -- and that
page is device-API oriented, so the platform REST envelope is **unverified**.
No platform-REST 4xx body example appears anywhere on docs.clover.com. This
table therefore emits ``message`` on every error and ``type`` only on the one
row whose value that page documents; inventing a ``type`` vocabulary for the
other nineteen would be this fake asserting an enum Clover does not publish.

The 401 conflation -- DOCUMENTED, and the headline fidelity point
-----------------------------------------------------------------
"The API does not distinguish between an unauthorized error (401 -
expired/invalid token) and a permissions error (403 - token has insufficient
permissions) and returns a 401 Unauthorized in either case."
https://docs.clover.com/dev/docs/401-unauthorized

So ``unauthorized``, ``token_expired``, ``token_revoked`` **and**
``forbidden_scope`` all answer **401** here -- there is no 403 row in this
table at all. A Square-habituated consumer expects a 403 on an under-permitted
token; surfacing that Clover does not send one is exactly the kind of
difference this fake exists to teach.

The conflation goes one step further than the status. The kernel raises
``forbidden_scope`` with a detail that names the missing permission ("The
access token is missing the required permission(s): ORDERS_W."), and the
chaos engine raises ``token_expired`` with its own; a Clover that put either
on the wire would be distinguishing the two cases it documents itself as not
distinguishing. So on ``token_expired``, ``token_revoked`` and
``forbidden_scope`` -- the kinds only the bearer path produces -- the wire
``message`` is **always the table's**, and the detail travels only in the
``unit_error`` sidecar (as ``detail``), for whoever is debugging this fake.

``unauthorized`` keeps the handler's detail, deliberately. Nothing in the core
raises it; the bearer adapter raises it **without** a detail (so the table
wins there too, and the three bearer failures are byte-identical on the wire
-- pinned by a test that drives the kernel's real permission check), and the
only callers that attach one are the OAuth *endpoints*, whose refusal bodies
("Failed to validate authentication code") are this package's JUDGMENT for a
gap the 401 sentence does not cover: that sentence is about API requests
carrying a bearer token, not about the token endpoint's own answers.

Rate limiting
-------------
429 is documented (https://docs.clover.com/dev/docs/api-usage-rate-limits)
with ``X-RateLimit-*`` headers and, on concurrent-limit trips, ``retry-after``
-- but no 429 *body* is published anywhere, so the message below is JUDGMENT.
This unit's 429s are chaos-injected only (no real rate accounting), and the
``retry-after`` header is switchable for the same reason Square's is.

The four ``X-RateLimit-*`` headers and their numbers -- 16 req/s per token,
50 per app, 5 concurrent per token, 10 per app -- are all documented on the
rate-limits page. JUDGMENT on the *emission*: Clover does not say which
headers accompany which kind of trip, and a chaos-injected 429 tripped no real
limit, so this unit stamps all four documented limits as constants on every
429 rather than inventing a story about which one fired.

The ``unit_error`` sidecar is a deliberate, namespaced deviation from Clover's
wire format: a consumer that reads only ``message`` never sees it, and a
consumer debugging this fake gets the machine-readable reason without parsing
prose. It is off with ``"error_sidecar": false`` in a profile's ``vendor``
block.
"""

from __future__ import annotations

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

__all__ = [
    "CLOVER_ERROR_TABLE",
    "CONFLATED_401_KINDS",
    "DETAIL_SUPPRESSED_KINDS",
    "CloverErrorMapping",
    "CloverErrorShaper",
    "Provenance",
]

_CONFLATED_401 = (
    "Clover: 'The API does not distinguish between an unauthorized error (401 - expired/invalid token) "
    "and a permissions error (403 - token has insufficient permissions) and returns a 401 Unauthorized "
    "in either case.' (https://docs.clover.com/dev/docs/401-unauthorized)"
)

_ENVELOPE_NOTE = (
    "The {message, type} envelope is evidenced only on the device-oriented status-code reference "
    "(https://docs.clover.com/dev/docs/status-code-and-error-reference); the platform-REST error body "
    "is not documented anywhere, so the shape itself is a judgment call."
)


@dataclass(frozen=True, slots=True)
class CloverErrorMapping:
    """One row: what a core error kind looks like on Clover's wire."""

    status: int
    #: Where the status comes from; surfaced in the sidecar and by
    #: ``GET /__unit/errors``.
    provenance: Provenance
    message: str
    #: The ``type`` field, emitted only where a documented value exists.
    type: str | None = None
    #: Set only where "judgment" understates the gap. See the module docstring.
    note: str | None = None

    def as_json(self) -> dict[str, Any]:
        """The row as a report publishes it."""
        return compact(
            {
                "status": self.status,
                "provenance": self.provenance,
                "message": self.message,
                "type": self.type,
                "note": self.note,
            }
        )


CLOVER_ERROR_TABLE: dict[UnitErrorKind, CloverErrorMapping] = {
    UnitErrorKind.BAD_REQUEST: CloverErrorMapping(
        status=400,
        provenance="judgment",
        message="Bad request.",
    ),
    UnitErrorKind.INVALID_JSON: CloverErrorMapping(
        status=400,
        provenance="judgment",
        message="The request body is not valid JSON.",
    ),
    UnitErrorKind.MISSING_FIELD: CloverErrorMapping(
        status=400,
        provenance="judgment",
        message="A required field is missing.",
    ),
    UnitErrorKind.INVALID_VALUE: CloverErrorMapping(
        status=400,
        provenance="judgment",
        message="The provided value is invalid.",
    ),
    UnitErrorKind.NOT_FOUND: CloverErrorMapping(
        status=404,
        provenance="judgment",
        message="Not found.",
    ),
    UnitErrorKind.METHOD_NOT_ALLOWED: CloverErrorMapping(
        status=405,
        provenance="judgment",
        message="The HTTP method is not allowed on this resource.",
    ),
    UnitErrorKind.UNAUTHORIZED: CloverErrorMapping(
        status=401,
        provenance="documented",
        message="401 Unauthorized",
        note=_CONFLATED_401,
    ),
    UnitErrorKind.TOKEN_EXPIRED: CloverErrorMapping(
        status=401,
        provenance="documented",
        message="401 Unauthorized",
        note=_CONFLATED_401,
    ),
    UnitErrorKind.TOKEN_REVOKED: CloverErrorMapping(
        status=401,
        provenance="documented",
        message="401 Unauthorized",
        note=_CONFLATED_401,
    ),
    # NOT 403. This is the documented conflation the module docstring quotes:
    # an under-permitted token gets the same 401 an invalid one does.
    UnitErrorKind.FORBIDDEN_SCOPE: CloverErrorMapping(
        status=401,
        provenance="documented",
        message="401 Unauthorized",
        note=_CONFLATED_401,
    ),
    UnitErrorKind.CAPABILITY_DISABLED: CloverErrorMapping(
        status=501,
        provenance="judgment",
        message="This capability is not enabled on this unit.",
    ),
    UnitErrorKind.VERSION_CONFLICT: CloverErrorMapping(
        status=409,
        provenance="judgment",
        message="The supplied version does not match the current version.",
    ),
    UnitErrorKind.IDEMPOTENCY_CONFLICT: CloverErrorMapping(
        status=409,
        provenance="judgment",
        message="A conflicting request with the same key was already processed.",
    ),
    UnitErrorKind.INVALID_CURSOR: CloverErrorMapping(
        status=400,
        provenance="judgment",
        message="The provided pagination offset is not valid.",
    ),
    UnitErrorKind.INVALID_TRANSITION: CloverErrorMapping(
        status=400,
        provenance="judgment",
        message="The order cannot be updated in its current state.",
    ),
    # The one row with a documented `type`: the status-code reference's own
    # example is {"message": "Conflicting request...", "type": "RESOURCE_CONFLICT"}.
    # https://docs.clover.com/dev/docs/status-code-and-error-reference
    UnitErrorKind.CONFLICT: CloverErrorMapping(
        status=409,
        provenance="documented",
        message="Conflicting request.",
        type="RESOURCE_CONFLICT",
        note=_ENVELOPE_NOTE,
    ),
    UnitErrorKind.RATE_LIMITED: CloverErrorMapping(
        status=429,
        provenance="documented",
        message="429 Too Many Requests",
        note=(
            "The 429 status and its X-RateLimit-* headers are documented "
            "(https://docs.clover.com/dev/docs/api-usage-rate-limits); the body is not, "
            "so the message is a judgment call."
        ),
    ),
    UnitErrorKind.TIMEOUT: CloverErrorMapping(
        status=504,
        provenance="judgment",
        message="Gateway timeout.",
    ),
    UnitErrorKind.UNAVAILABLE: CloverErrorMapping(
        status=503,
        provenance="judgment",
        message="Service unavailable.",
    ),
    UnitErrorKind.INTERNAL: CloverErrorMapping(
        status=500,
        provenance="judgment",
        message="Internal server error.",
    ),
}
"""Twenty rows, one per core error kind. See the module docstring for
provenance -- and note there is deliberately no 403 anywhere in this table."""

CONFLATED_401_KINDS: frozenset[UnitErrorKind] = frozenset(
    {
        UnitErrorKind.UNAUTHORIZED,
        UnitErrorKind.TOKEN_EXPIRED,
        UnitErrorKind.TOKEN_REVOKED,
        UnitErrorKind.FORBIDDEN_SCOPE,
    }
)
"""The four rows the documented conflation collapses onto 401."""

DETAIL_SUPPRESSED_KINDS: frozenset[UnitErrorKind] = frozenset(
    {
        UnitErrorKind.TOKEN_EXPIRED,
        UnitErrorKind.TOKEN_REVOKED,
        UnitErrorKind.FORBIDDEN_SCOPE,
    }
)
"""The conflated rows whose wire message ignores the error's own detail: the
kinds only the bearer path produces. ``unauthorized`` is conflated in status
but keeps its detail -- see the module docstring for why."""

_RATE_LIMIT_HEADERS: dict[str, str] = {
    "x-ratelimit-tokenlimit": "16",
    "x-ratelimit-crosstokenlimit": "50",
    "x-ratelimit-tokenconcurrentlimit": "5",
    "x-ratelimit-crosstokenconcurrentlimit": "10",
}
"""The four documented rate-limit headers with the documented limits: "50
requests per second per app, 16 requests per second per token; concurrent
limits of 10 per app and 5 per token"
(https://docs.clover.com/dev/docs/api-usage-rate-limits). JUDGMENT on stamping
all four on every 429; see the module docstring."""


class CloverErrorShaper:
    """Turns a :class:`UnitError` into Clover's envelope. Satisfies ``ErrorShaper``.

    Frozen configuration rather than a live read of the profile, because the
    vendor rebuilds the shaper when its configuration resolves; a shaper that
    reached back into a context to ask whether the sidecar was on would have to
    do it on every error.
    """

    __slots__ = ("_retry_after_header", "_sidecar")

    def __init__(self, *, sidecar: bool = True, retry_after_header: bool = True) -> None:
        self._sidecar = sidecar
        self._retry_after_header = retry_after_header

    def shape(self, err: UnitError, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """One core error, as this unit's Clover would send it.

        ``describing`` is accepted and ignored: this envelope carries no
        per-request id or timestamp, so a described body and a real one are
        already the same bytes.

        ``message`` follows the error's own wording when it has one and the
        table's otherwise, so a handler that explains precisely what was wrong
        is not overwritten by a generic sentence -- except on the bearer-path
        401 rows (:data:`DETAIL_SUPPRESSED_KINDS`), where the table always
        wins and the detail goes to the sidecar instead (module docstring).
        ``type`` is emitted only on the one row whose value Clover documents.
        """
        mapping = CLOVER_ERROR_TABLE[err.kind]
        conflated = err.kind in DETAIL_SUPPRESSED_KINDS
        message = mapping.message if conflated or not err.detail else err.detail
        body: dict[str, Any] = compact({"message": message, "type": mapping.type})
        if self._sidecar:
            body["unit_error"] = unit_error_sidecar(
                err,
                mapping.provenance,
                field=err.field or None,
                detail=(err.detail or None) if conflated else None,
            )
        headers: dict[str, str] = {}
        if err.kind is UnitErrorKind.RATE_LIMITED:
            headers.update(_RATE_LIMIT_HEADERS)
        headers.update(mechanism_headers(err, retry_after_header=self._retry_after_header))
        return ShapedError(status=mapping.status, body=body, headers=headers)

    def not_found(self, req: UnitRequest, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """The body for a path that matched no route at all.

        It names the control route that lists the surface, because the most
        common cause of a 404 against a fake is a profile that does not serve
        the capability the caller assumed.

        ``describing`` is accepted and ignored: Clover's envelope carries no
        per-request id or timestamp, so a described body and a real one are
        already the same bytes.
        """
        return self.shape(
            UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=(
                    f"{req.method} {req.path} is not a route on this Clover unit. "
                    "GET /__unit/routes lists the surface this profile serves."
                ),
                info={"path": req.path, "method": req.method, "profile": ctx.config.profile},
            ),
            ctx,
        )

    def describe(self) -> dict[str, dict[str, Any]]:
        """The table as a report publishes it -- twenty rows with provenance."""
        return {kind.value: mapping.as_json() for kind, mapping in CLOVER_ERROR_TABLE.items()}


# Exhaustiveness, at import, as a raise and never as an `assert` -- see
# core/kernel/shaping.py for why.
assert_error_table_total(CLOVER_ERROR_TABLE, name="CLOVER_ERROR_TABLE")
