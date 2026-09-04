"""Who a presented credential is, in Square's two documented schemes.

INVARIANT: token validity is not gated by the ``oauth`` capability -- a profile with the OAuth dance off
still authenticates a seeded token. DOCUMENTED: ``bearer`` is ``Authorization: Bearer {ACCESS_TOKEN}``;
``client-secret`` is ``Authorization: Client {APPLICATION_SECRET}`` on ``POST /oauth2/revoke``
(https://developer.squareup.com/reference/square/oauth-api/revoke-token).
https://developer.squareup.com/docs/build-basics/access-tokens
DOCUMENTED: a superseded token still authenticates -- a code-flow refresh marks the previous record
``superseded_at``, deliberately not consulted here (a refresh token can mint multiple active access
tokens, https://developer.squareup.com/docs/oauth-api/overview). Only ``revoked_at`` and the clock end
a token.
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
from vendorfake.square.entities import COL, TokenEntity
from vendorfake.square.surface.common import SquareDeps, is_expired

__all__ = ["BEARER_SCHEME", "CLIENT_SCHEME", "SquareAuth"]

BEARER_SCHEME = "Bearer"
CLIENT_SCHEME = "Client"

_INCORRECT = "The `Authorization` http header of your request was incorrect or expired."
"""Square's own wording for a header it will not accept."""


def _split_scheme(header: str) -> tuple[str, str]:
    """``"Bearer abc def"`` -> ``("Bearer", "abc def")``; the credential keeps its inner spaces."""
    scheme, separator, credential = header.partition(" ")
    return (scheme, credential if separator else "")


class SquareAuth:
    """Square's authentication. Satisfies ``AuthAdapter``. Holds the vendor, since the secret is
    re-resolved on every hydrate."""

    __slots__ = ("_deps", "_scopes")

    def __init__(self, deps: SquareDeps, scopes: tuple[str, ...]) -> None:
        self._deps = deps
        self._scopes = scopes

    def describe(self) -> Mapping[str, str]:
        """What ``GET /__unit/info`` publishes about authenticating here."""
        return {
            "bearer": ("Authorization: Bearer {ACCESS_TOKEN} (developer.squareup.com/docs/build-basics/access-tokens)"),
            "client-secret": (
                "Authorization: Client {APPLICATION_SECRET} "
                "(developer.squareup.com/reference/square/oauth-api/revoke-token)"
            ),
            "scopes": " ".join(self._scopes),
        }

    def credentials(self, ctx: UnitContext) -> Sequence[AuthCredential]:
        """Every credential this unit would currently accept, read from the store, not the seed."""
        offered: list[AuthCredential] = [
            AuthCredential(
                label="client-secret",
                mode="client-secret",
                headers={"authorization": f"{CLIENT_SCHEME} {self._deps.config.application_secret}"},
                scopes=self._scopes,
                summary="The application secret, which POST /oauth2/revoke authenticates with.",
            )
        ]
        for entity in ctx.store.collection(COL.tokens).all():
            token = TokenEntity.from_entity(entity)
            if token.revoked_at is not None or is_expired(token.expires_at, ctx.clock):
                continue
            offered.append(
                AuthCredential(
                    label=token.id,
                    mode="bearer",
                    headers={"authorization": f"{BEARER_SCHEME} {token.access_token}"},
                    scopes=token.scopes,
                    summary=f"Access token for merchant {token.merchant_id}, expiring {token.expires_at}.",
                )
            )
        return tuple(offered)

    def resolve(self, args: HandlerArgs, mode: AuthMode) -> AuthResult:
        header = args.header("authorization")
        if not header:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, detail=_INCORRECT, info={"expected": mode})
        if mode == "client-secret":
            return self._resolve_client(header)
        return self._resolve_bearer(args, header)

    # -- the two schemes ---------------------------------------------------

    def _resolve_client(self, header: str) -> AuthResult:
        scheme, secret = _split_scheme(header)
        if scheme != CLIENT_SCHEME or secret != self._deps.config.application_secret:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail=_INCORRECT,
                info={"expected": "Authorization: Client {APPLICATION_SECRET}"},
            )
        # The application, not a merchant: a client-secret call has no seller identity.
        return AuthResult(principal_id="application", scopes=self._scopes, meta={"mode": "client-secret"})

    def _resolve_bearer(self, args: HandlerArgs, header: str) -> AuthResult:
        scheme, value = _split_scheme(header)
        if scheme != BEARER_SCHEME or not value:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail=_INCORRECT,
                info={"expected": "Authorization: Bearer {ACCESS_TOKEN}"},
            )
        found = args.ctx.store.collection(COL.tokens).find(lambda entity: entity.get("access_token") == value)
        if found is None:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, detail="This request could not be authorized.")
        token = TokenEntity.from_entity(found)
        if token.revoked_at is not None:
            raise UnitError(UnitErrorKind.TOKEN_REVOKED, detail="The provided access token has been revoked.")
        if is_expired(token.expires_at, args.ctx.clock):
            raise UnitError(UnitErrorKind.TOKEN_EXPIRED, detail="The provided access token has expired.")
        return AuthResult(
            principal_id=token.merchant_id,
            scopes=token.scopes,
            token_id=token.id,
            meta={"client_id": token.client_id, "short_lived": token.short_lived, "flow": token.flow},
        )
