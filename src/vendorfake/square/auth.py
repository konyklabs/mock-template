"""Who a presented credential is, in Square's two documented schemes.

FOR: turning an ``Authorization`` header into an
:class:`~vendorfake.core.kernel.types.AuthResult` the kernel can check scopes
against, and producing the three distinct 401s Square publishes rather than one
generic refusal.

INVARIANT: **token validity is not gated by the ``oauth`` capability.** A
profile with the OAuth dance switched off still authenticates a seeded token,
so a consumer testing orders is never forced to run an authorization flow it
does not care about. That is why this module is reachable from every route's
``auth`` mode while ``/oauth2/*`` lives behind the capability.

The three failure codes are documented, not invented:
``UNAUTHORIZED``, ``ACCESS_TOKEN_REVOKED`` and ``ACCESS_TOKEN_EXPIRED`` all
appear on https://developer.squareup.com/docs/build-basics/handling-errors, and
the detail string "This request could not be authorized." is the verbatim
detail from Square's own example error body. The two schemes are

``bearer``
    ``Authorization: Bearer {ACCESS_TOKEN}`` on every v2 call.
    https://developer.squareup.com/docs/build-basics/access-tokens

``client-secret``
    ``Authorization: Client {APPLICATION_SECRET}`` on ``POST /oauth2/revoke``.
    https://developer.squareup.com/reference/square/oauth-api/revoke-token

Two 401/403 codes Square documents are unreachable here and recorded rather
than faked: ``CLIENT_DISABLED``, which describes an application state this unit
has no way to enter, and the general ``FORBIDDEN``, which this unit only ever
produces as a scope failure.

A superseded token still authenticates
--------------------------------------
A code-flow refresh marks the previous token record ``superseded_at`` so the
refresh lookup stays single-valued, and that mark is deliberately **not**
consulted here: "A refresh token obtained using the code flow can be used to
get multiple active access tokens"
(https://developer.squareup.com/docs/oauth-api/overview), so the older access
token stays valid until its own expiry. Only ``revoked_at`` and the clock end a
token.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.kernel.types import (
    AuthMode,
    AuthResult,
    HandlerArgs,
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
    """``"Bearer abc def"`` -> ``("Bearer", "abc def")``.

    The credential keeps its inner spaces, matching the reference's
    ``rest.join(' ')``: a token is opaque, and splitting on every space would
    make this unit reject a credential a real client had pasted intact.
    """
    scheme, separator, credential = header.partition(" ")
    return (scheme, credential if separator else "")


class SquareAuth:
    """Square's authentication. Satisfies ``AuthAdapter``.

    Holds the vendor rather than the secret, because the secret is resolved
    again on every hydrate: a copy taken at construction would be the default
    long after a profile replaced it.
    """

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
        # The application, not a merchant: revocation acts on behalf of the
        # application and there is no seller identity in a client-secret call.
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
