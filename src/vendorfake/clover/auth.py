"""Who a presented credential is: Clover's one documented scheme, bearer
tokens only (https://docs.clover.com/dev/docs/401-unauthorized).

DOCUMENTED CONFLATION: Clover returns 401 for both an invalid/expired token
and an under-permitted one, never 403; every refusal here raises without a
detail so the wire stays a bare 401, while ``unit_error`` carries the real
reason for debugging. Permissions come from the token record, inherited from
the app's fixed set at mint (``config.py``). JUDGMENT: rotating a refresh
token does not end its access token (Clover documents rotation as
invalidating only the refresh token, see ``surface/oauth.py``); there is no
revoke endpoint, so only the clock ends a token.
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
    """``"Bearer abc def"`` -> ``("Bearer", "abc def")``."""
    scheme, separator, credential = header.partition(" ")
    return (scheme, credential if separator else "")


class CloverAuth:
    """Clover's authentication. Satisfies ``AuthAdapter``."""

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
        """Every currently valid credential, read live so an ended token drops out."""
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
        """Resolve the bearer, or raise with no detail (wire stays a bare 401)."""
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
