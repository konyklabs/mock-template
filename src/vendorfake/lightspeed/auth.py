"""Who a presented credential is, and what it may do.

FOR: turning an ``Authorization: Bearer`` header into an
:class:`~vendorfake.core.kernel.types.AuthResult` the kernel can check required
scopes against.

DOCUMENTED: the whole specification uses ONE security scheme --
``{"bearerAuth": {"scheme": "bearer", "type": "http"}}`` in
``components.securitySchemes``, applied globally at the document root as
``"security": [{"bearerAuth": []}]``, with no per-operation override anywhere
in the 201 operations. There is no second mode. Personal tokens are "applied
identically to OAuth tokens via the Authorization header"
(https://x-series-api.lightspeedhq.com/docs/authorization), so they resolve
through the same path and differ only in their record's ``kind``.

DOCUMENTED: scopes. Each operation names the scope it needs in its own
``description``, in the pattern ``🔒 Requires: `scope:name` scope``. The kernel
checks ``Route.scopes`` against the resolved result and raises
``forbidden_scope``, which the error table maps to 403 (JUDGMENT on the
status -- see ``errors.py``), so this adapter only reports scopes.

DOCUMENTED: revocation. "Using a refresh token will revoke the access token
that was returned with it." A revoked token is refused here, distinctly from
an expired one, so a consumer's refresh handling can tell the two apart
through the ``Vendorfake-Error-Kind`` sidecar header even though both answer
the same documented 401.

**THE RATE LIMITER RUNS HERE**, before the token is even looked at. The
documented quota counts *requests* per retailer per application, not successful
ones, and every authenticated route in this surface passes through this method.
The two unauthenticated routes count in their own handlers; the control plane
does not count at all. See ``ratelimit.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from vendorfake.core.kernel.types import (
    AuthCredential,
    AuthMode,
    AuthResult,
    HandlerArgs,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.lightspeed.entities import COL, RetailerEntity, TokenEntity
from vendorfake.lightspeed.model.auth import SCOPE_SEPARATOR, TOKEN_TYPE
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, is_past_ms

__all__ = ["KIND_OAUTH", "KIND_PERSONAL", "LightspeedAuth", "find_token"]

KIND_OAUTH = "oauth"
KIND_PERSONAL = "personal"
"""The two kinds of bearer the vendor documents. Both authenticate identically;
the kind is recorded so a scenario can tell them apart and so
``/__unit/auth`` can say which is which."""


def _split_scheme(header: str) -> tuple[str, str]:
    scheme, separator, credential = header.partition(" ")
    return (scheme, credential.strip() if separator else "")


def find_token(ctx: UnitContext, access_token: str) -> TokenEntity | None:
    """The stored token for an opaque bearer string, or ``None``.

    One collection holds every kind: an OAuth token from the exchange or a
    refresh, and a seeded personal token. Lightspeed applies both "identically
    via the Authorization header", so a second lookup keyed on kind would be a
    second place for the two to drift apart.
    """
    found = ctx.store.collection(COL.tokens).find(lambda entity: entity.get("access_token") == access_token)
    return None if found is None else TokenEntity.from_entity(found)


class LightspeedAuth:
    """Lightspeed's authentication. Satisfies ``AuthAdapter``."""

    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def describe(self) -> Mapping[str, str]:
        return {
            "bearer": (
                f"Authorization: {TOKEN_TYPE} {{access_token}} from POST /api/1.0/token, or a seeded personal "
                "token. One flat bearerAuth scheme, applied to every operation in the document."
            ),
            "grants": "authorization_code and refresh_token; refreshing rotates and revokes the old access token",
            "scopes": SCOPE_SEPARATOR.join(self._deps.config.scopes),
            "domain_prefix": self._deps.config.domain_prefix,
            "refusals": (
                "401 token missing/invalid/expired/revoked; 403 missing scope; 429 when the documented "
                "300 x registers + 50 quota is spent in a 5-minute window."
            ),
        }

    def credentials(self, ctx: UnitContext) -> Sequence[AuthCredential]:
        """Every live token, read from the store -- so a token an exchange just
        minted is offered and one the clock has ended, or a refresh has
        revoked, is not."""
        offered: list[AuthCredential] = []
        for entity in ctx.store.collection(COL.tokens).all():
            token = TokenEntity.from_entity(entity)
            if token.revoked_at_ms is not None or is_past_ms(token.expires_at_ms, ctx.clock):
                continue
            offered.append(
                AuthCredential(
                    label=token.id,
                    mode=BEARER_AUTH,
                    headers={"authorization": f"{TOKEN_TYPE} {token.access_token}"},
                    scopes=token.scopes,
                    summary=(
                        f"{'Personal' if token.kind == KIND_PERSONAL else 'OAuth'} token for client {token.client_id}."
                    ),
                )
            )
        return tuple(offered)

    def resolve(self, args: HandlerArgs, mode: AuthMode) -> AuthResult:
        # Counted before the credential is examined: the documented limiter
        # counts requests, and a caller hammering with a bad token is still
        # spending the retailer's quota.
        self._deps.limiter.consume(args.ctx)
        header = args.header("authorization")
        if not header:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail=f"An Authorization: {TOKEN_TYPE} header is required.",
                info={"reason": "no_authorization_header"},
            )
        scheme, value = _split_scheme(header)
        if scheme != TOKEN_TYPE or not value:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail=f"The Authorization header must use the {TOKEN_TYPE} scheme.",
                info={"reason": "not_a_bearer_header"},
            )
        token = find_token(args.ctx, value)
        if token is None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The access token is not valid.",
                info={"reason": "unknown_token"},
            )
        if token.revoked_at_ms is not None:
            raise UnitError(
                UnitErrorKind.TOKEN_REVOKED,
                detail=(
                    "The access token was revoked: using a refresh token revokes the access token that was "
                    "returned with it."
                ),
                info={"reason": "revoked_by_refresh"},
            )
        if is_past_ms(token.expires_at_ms, args.ctx.clock):
            raise UnitError(
                UnitErrorKind.TOKEN_EXPIRED,
                detail="The access token has expired; refresh it or authorize again.",
                info={"reason": "access_token_expired"},
            )
        retailer = _the_retailer(args.ctx)
        return AuthResult(
            principal_id=retailer.id if retailer is not None else token.client_id,
            scopes=token.scopes,
            token_id=token.id,
            meta={"client_id": token.client_id, "kind": token.kind, "domain_prefix": self._deps.config.domain_prefix},
        )


def _the_retailer(ctx: UnitContext) -> RetailerEntity | None:
    rows = ctx.store.collection(COL.retailer).all()
    return RetailerEntity.from_entity(rows[0]) if rows else None
