"""Who a presented credential is, in Clover's one documented scheme.

FOR: turning an ``Authorization`` header into an
:class:`~vendorfake.core.kernel.types.AuthResult` the kernel can check
required permissions against. One scheme only: "API requests must be
authenticated using the Authorization Bearer header"
(https://docs.clover.com/dev/docs/401-unauthorized).

THE DOCUMENTED CONFLATION, and how this adapter implements it: "The API does
not distinguish between an unauthorized error (401 - expired/invalid token)
and a permissions error (403 - token has insufficient permissions) and
returns a 401 Unauthorized in either case."
https://docs.clover.com/dev/docs/401-unauthorized

Every refusal below raises its core kind **without a detail**, so the error
table's message -- the same ``"401 Unauthorized"`` on every one of those rows
-- is what goes on the wire. A missing header, an unknown token, an expired
token and (via the kernel's own permission check raising ``forbidden_scope``)
an under-permitted token are therefore *indistinguishable on Clover's wire*,
exactly as documented; the ``unit_error`` sidecar carries the distinction for
whoever is debugging this fake. There is no 403 anywhere in this vendor.

Permissions come from the token record, which inherited the app's fixed set
at mint (Clover permissions are app-level, set in the dashboard, not
requested per token -- see ``config.py``). The kernel checks
``Route.scopes`` against them; this adapter only reports them.

A rotated refresh token does not end an access token: ``refresh_used_at_ms``
is deliberately not consulted here. Clover documents rotation as invalidating
the *refresh* token only, and says nothing about access tokens minted
earlier (JUDGMENT, labelled in ``surface/oauth.py``). Only the clock ends a
token -- there is no revoke endpoint in Clover's v2 OAuth at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from vendorfake.clover.entities import COL, TokenEntity
from vendorfake.clover.surface.common import CloverDeps, is_past_ms
from vendorfake.core.kernel.types import (
    AuthCredential,
    AuthMode,
    AuthResult,
    HandlerArgs,
    UnitContext,
    UnitError,
    UnitErrorKind,
)

__all__ = ["BEARER_SCHEME", "CloverAuth"]

BEARER_SCHEME = "Bearer"


def _split_scheme(header: str) -> tuple[str, str]:
    """``"Bearer abc def"`` -> ``("Bearer", "abc def")``. The credential keeps
    its inner spaces: a token is opaque."""
    scheme, separator, credential = header.partition(" ")
    return (scheme, credential if separator else "")


class CloverAuth:
    """Clover's authentication. Satisfies ``AuthAdapter``.

    Holds the vendor rather than a copy of its configuration, because the
    permission vocabulary resolves again on every hydrate.
    """

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def describe(self) -> Mapping[str, str]:
        """What ``GET /__unit/info`` publishes about authenticating here."""
        return {
            "bearer": "Authorization: Bearer {ACCESS_TOKEN} (docs.clover.com/dev/docs/401-unauthorized)",
            "permissions": " ".join(self._deps.config.permissions),
            "conflation": (
                "Bad token and insufficient permission both answer 401; Clover documents no 403. "
                "(docs.clover.com/dev/docs/401-unauthorized)"
            ),
        }

    def credentials(self, ctx: UnitContext) -> Sequence[AuthCredential]:
        """Every credential this unit would currently accept.

        Read out of the store rather than out of any seed, so a token minted
        by an OAuth flow a moment ago is offered and one the clock has ended
        is not. Rotated-refresh records still appear when their access token
        is alive, because that access token still authenticates -- offering it
        is the honest answer.
        """
        offered: list[AuthCredential] = []
        for entity in ctx.store.collection(COL.tokens).all():
            token = TokenEntity.from_entity(entity)
            if is_past_ms(token.access_token_expiration_ms, ctx.clock):
                continue
            offered.append(
                AuthCredential(
                    label=token.id,
                    mode="bearer",
                    headers={"authorization": f"{BEARER_SCHEME} {token.access_token}"},
                    scopes=token.permissions,
                    summary=f"Access token for merchant {token.merchant_id}; expires in 30 minutes from mint.",
                )
            )
        return tuple(offered)

    def resolve(self, args: HandlerArgs, mode: AuthMode) -> AuthResult:
        """Resolve the bearer, or raise a kind whose wire body is the one
        documented ``401 Unauthorized``. No detail on purpose -- see the
        module docstring."""
        header = args.header("authorization")
        if not header:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, info={"reason": "no_authorization_header"})
        scheme, value = _split_scheme(header)
        if scheme != BEARER_SCHEME or not value:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, info={"reason": "not_a_bearer_header"})
        found = args.ctx.store.collection(COL.tokens).find(lambda entity: entity.get("access_token") == value)
        if found is None:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, info={"reason": "unknown_token"})
        token = TokenEntity.from_entity(found)
        if is_past_ms(token.access_token_expiration_ms, args.ctx.clock):
            raise UnitError(UnitErrorKind.TOKEN_EXPIRED, info={"reason": "access_token_expired"})
        return AuthResult(
            principal_id=token.merchant_id,
            scopes=token.permissions,
            token_id=token.id,
            meta={"client_id": token.client_id},
        )
