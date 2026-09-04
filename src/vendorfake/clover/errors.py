"""Clover error shaping -- maps each of the core's twenty vendor-neutral
error kinds to an HTTP status and Clover's error envelope; exhaustiveness is
checked at import (bottom of this module). Provenance is published via the
``unit_error`` sidecar and by :meth:`CloverErrorShaper.describe`.

Envelope -- JUDGMENT: only the device-oriented status-code reference shows a
body (https://docs.clover.com/dev/docs/status-code-and-error-reference,
``{"message": "...", "type": "RESOURCE_CONFLICT"}``); no platform-REST body
is documented, so only that one row carries ``type``.

401 conflation -- DOCUMENTED: Clover returns 401 for both invalid-token and
insufficient-scope cases, with no 403
(https://docs.clover.com/dev/docs/401-unauthorized). ``unauthorized``,
``token_expired``, ``token_revoked`` and ``forbidden_scope`` all answer 401;
the three bearer-only kinds fix ``message`` to "401 Unauthorized" and push
their detail to the sidecar, while ``unauthorized`` keeps its detail because
it also covers OAuth's own refusals.

Rate limiting -- DOCUMENTED status and ``X-RateLimit-*`` headers
(https://docs.clover.com/dev/docs/api-usage-rate-limits); JUDGMENT on the
body and on stamping all four headers on every chaos-injected 429.

``unit_error`` sidecar: this project's diagnostic surface, off via
``"error_sidecar": false``, riding as headers by default (``errors.sidecar``,
konyklabs/roadmap#71).
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
    # NOT 403 -- the documented conflation; see the module docstring.
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
    # The one row with a documented `type`; see the module docstring.
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
"""Twenty rows, one per core error kind; no 403 anywhere. See the module
docstring for provenance."""

CONFLATED_401_KINDS: frozenset[UnitErrorKind] = frozenset(
    {
        UnitErrorKind.UNAUTHORIZED,
        UnitErrorKind.TOKEN_EXPIRED,
        UnitErrorKind.TOKEN_REVOKED,
        UnitErrorKind.FORBIDDEN_SCOPE,
    }
)
"""The rows the documented conflation collapses onto 401."""

DETAIL_SUPPRESSED_KINDS: frozenset[UnitErrorKind] = frozenset(
    {
        UnitErrorKind.TOKEN_EXPIRED,
        UnitErrorKind.TOKEN_REVOKED,
        UnitErrorKind.FORBIDDEN_SCOPE,
    }
)
"""Conflated rows whose wire message ignores the error's own detail; see the
module docstring for why ``unauthorized`` is excluded."""

_RATE_LIMIT_HEADERS: dict[str, str] = {
    "x-ratelimit-tokenlimit": "16",
    "x-ratelimit-crosstokenlimit": "50",
    "x-ratelimit-tokenconcurrentlimit": "5",
    "x-ratelimit-crosstokenconcurrentlimit": "10",
}
"""The four documented rate-limit headers and limits
(https://docs.clover.com/dev/docs/api-usage-rate-limits). JUDGMENT on
stamping all four on every 429; see the module docstring."""


class CloverErrorShaper:
    """Turns a :class:`UnitError` into Clover's envelope. Satisfies ``ErrorShaper``.

    Frozen configuration rather than a live read of the profile: the vendor
    rebuilds the shaper whenever its configuration resolves.
    """

    __slots__ = ("_retry_after_header", "_sidecar")

    def __init__(self, *, sidecar: bool = True, retry_after_header: bool = True) -> None:
        self._sidecar = sidecar
        self._retry_after_header = retry_after_header

    def shape(self, err: UnitError, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """One core error, as this unit's Clover would send it.

        ``describing`` is ignored: the envelope has no per-request id or
        timestamp. ``message`` follows the error's detail except on
        :data:`DETAIL_SUPPRESSED_KINDS`, where the table wins and detail goes
        to the sidecar instead.
        """
        mapping = CLOVER_ERROR_TABLE[err.kind]
        conflated = err.kind in DETAIL_SUPPRESSED_KINDS
        message = mapping.message if conflated or not err.detail else err.detail
        body: dict[str, Any] = compact({"message": message, "type": mapping.type})
        headers: dict[str, str] = {}
        if err.kind is UnitErrorKind.RATE_LIMITED:
            headers.update(_RATE_LIMIT_HEADERS)
        headers.update(mechanism_headers(err, retry_after_header=self._retry_after_header))
        if self._sidecar:
            # Where it rides is `ctx.config.errors.sidecar`, not this
            # constructor (konyklabs/roadmap#71).
            sidecar = unit_error_sidecar(
                err,
                mapping.provenance,
                field=err.field or None,
                detail=(err.detail or None) if conflated else None,
            )
            mode = ctx.config.errors.sidecar
            if mode != "headers":
                body["unit_error"] = sidecar
            if mode != "body":
                headers.update(sidecar_headers(sidecar))
        return ShapedError(status=mapping.status, body=body, headers=headers)

    def not_found(self, req: UnitRequest, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """404 body naming the control route that lists this profile's surface."""
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
