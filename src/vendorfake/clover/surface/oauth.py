"""The OAuth v2 surface: authorize, token exchange, refresh.

DOCUMENTED: authorize redirects with ``merchant_id``, ``client_id``,
``code``; token exchange answers the four-field
``{access_token, access_token_expiration, refresh_token,
refresh_token_expiration}``; refresh is ``{client_id, refresh_token}``,
single-use, no secret
(https://docs.clover.com/dev/docs/high-trust-app-auth-flow,
https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token,
https://docs.clover.com/dev/docs/refresh-access-tokens,
https://docs.clover.com/dev/docs/oauth-and-tokens-faqs). Everything else
(error bodies/status, PKCE via RFC 7636, code TTL) is JUDGMENT, labelled at
each site.

Invariant: no 4xx leaves a journal entry -- every refusal is checked before
its write.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from vendorfake.clover.entities import COL, AuthorizationCodeEntity, MerchantEntity, TokenEntity
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.oauth import RefreshRequest, TokenRequest, TokenResponse
from vendorfake.clover.surface.common import CloverDeps, is_past_ms, wire_seconds
from vendorfake.core.kernel.reply import json_, redirect
from vendorfake.core.kernel.types import (
    HandlerArgs,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.state.store import Entity
from vendorfake.core.util.b64 import b64url_encode

__all__ = ["CAPABILITY", "FAILED_CODE_MESSAGE", "CloverOAuthSurface", "oauth_routes"]

CAPABILITY = "oauth"
"""The capability every route below belongs to."""

FAILED_CODE_MESSAGE = "Failed to validate authentication code"
"""DOCUMENTED: Clover's OAuth FAQ phrase, used for any bad code
(https://docs.clover.com/dev/docs/oauth-and-tokens-faqs)."""


class CloverOAuthSurface:
    """The three OAuth routes, bound to one vendor's config and id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/oauth/v2/authorize",
                capability=CAPABILITY,
                handler=self.authorize,
                operation_id="Authorize",
                summary="Authorization page. Redirects back with merchant_id, client_id and a code.",
            ),
            Route(
                method="POST",
                path="/oauth/v2/token",
                capability=CAPABILITY,
                handler=self.exchange_token,
                operation_id="ExchangeToken",
                summary="Exchange an authorization code for the four-field token response.",
            ),
            Route(
                method="POST",
                path="/oauth/v2/refresh",
                capability=CAPABILITY,
                handler=self.refresh_token,
                operation_id="RefreshToken",
                summary="Rotate a single-use refresh token for a new access/refresh pair.",
            ),
        )

    # -- GET /oauth/v2/authorize -------------------------------------------

    def authorize(self, args: HandlerArgs) -> ReplyInit:
        """DOCUMENTED: redirects with ``?merchant_id=...&client_id=...&code=...``
        (https://docs.clover.com/dev/docs/high-trust-app-auth-flow); approval
        is automatic since a mock has nobody to click.
        """
        config = self._deps.config
        # `_query` refuses an empty value by name (model/common.py).
        client_id = _query(args, "client_id")
        if client_id is None:
            raise UnitError(UnitErrorKind.MISSING_FIELD, detail="client_id is required.", field="client_id")
        if client_id != config.client_id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Unknown client_id {client_id!r}. This unit is configured for app {config.client_id!r}.",
                field="client_id",
            )

        supplied_redirect = _query(args, "redirect_uri")
        if supplied_redirect is not None and supplied_redirect != config.redirect_uri:
            # JUDGMENT: blocks redirecting a code to an unregistered URI.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="redirect_uri does not match the redirect URI registered for this app.",
                field="redirect_uri",
            )
        redirect_uri = supplied_redirect or config.redirect_uri

        challenge = _query(args, "code_challenge")
        method = _query(args, "code_challenge_method")
        if challenge is not None and method != "S256":
            # JUDGMENT: an omitted method defaults to `plain` (RFC 7636 s4.3),
            # which is rejected rather than accepted.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    "code_challenge_method must be S256. An omitted method means 'plain' (RFC 7636 s4.3), "
                    "which is not supported."
                ),
                field="code_challenge_method",
            )

        merchant = _the_merchant(args.ctx)
        code = self._deps.ids.authorization_code()
        args.ctx.store.collection(COL.codes).insert(
            AuthorizationCodeEntity(
                id=code,
                client_id=client_id,
                merchant_id=merchant.id,
                expires_at_ms=int(args.ctx.clock.now()) + self._deps.config.authorization_code_ttl_ms,
                code_challenge=challenge,
            ).to_entity(),
            {"operation_id": "Authorize"},
        )
        params = {"merchant_id": merchant.id, "client_id": client_id, "code": code}
        state = args.query("state")
        if state is not None:
            params["state"] = state
        return redirect(_with_query(redirect_uri, params))

    # -- POST /oauth/v2/token ----------------------------------------------

    def exchange_token(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        request = validate_body(TokenRequest, args.body())
        self._check_client(request.client_id)

        codes = ctx.store.collection(COL.codes)
        stored = codes.get(request.code)
        if stored is None:
            raise _failed_code("unknown")
        record = AuthorizationCodeEntity.from_entity(stored)
        if record.client_id != request.client_id:
            # A second seeded app must not redeem the first app's code.
            raise _failed_code("other_client")
        if record.used_at_ms is not None:
            raise _failed_code("already_used")
        if is_past_ms(record.expires_at_ms, ctx.clock):
            raise _failed_code("expired")

        # All refusals precede the mark-used write (ordering invariant).
        if record.code_challenge is not None:
            self._check_verifier(request.code_verifier, record.code_challenge)
        else:
            self._check_secret(request.client_secret)

        now_ms = int(ctx.clock.now())

        def mark_used(draft: Entity) -> None:
            draft["used_at_ms"] = now_ms

        codes.update(record.id, mark_used, meta={"operation_id": "ExchangeToken"})
        return json_(
            self._mint(
                ctx,
                client_id=record.client_id,
                merchant_id=record.merchant_id,
                now_ms=now_ms,
                operation_id="ExchangeToken",
            )
        )

    # -- POST /oauth/v2/refresh --------------------------------------------

    def refresh_token(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        request = validate_body(RefreshRequest, args.body())
        self._check_client(request.client_id)

        tokens = ctx.store.collection(COL.tokens)
        found = tokens.find(lambda entity: entity.get("refresh_token") == request.refresh_token)
        if found is None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The refresh token is invalid.",
                field="refresh_token",
            )
        existing = TokenEntity.from_entity(found)
        if existing.client_id != request.client_id:
            # A second seeded app must not rotate the first app's grant.
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The refresh token was not issued to this client_id.",
                field="refresh_token",
            )
        if existing.refresh_used_at_ms is not None:
            # DOCUMENTED single-use: https://docs.clover.com/dev/docs/refresh-access-tokens
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The refresh token was already used. Refresh tokens are single use.",
                field="refresh_token",
            )
        if is_past_ms(existing.refresh_token_expiration_ms, ctx.clock):
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The refresh token expired.",
                field="refresh_token",
            )

        # All refusals are above the rotation write (ordering invariant).
        now_ms = int(ctx.clock.now())

        def rotate(draft: Entity) -> None:
            draft["refresh_used_at_ms"] = now_ms

        tokens.update(existing.id, rotate, meta={"operation_id": "RefreshToken"})
        return json_(
            self._mint(
                ctx,
                client_id=existing.client_id,
                merchant_id=existing.merchant_id,
                now_ms=now_ms,
                operation_id="RefreshToken",
            )
        )

    # -- shared ------------------------------------------------------------

    def _check_client(self, client_id: str) -> None:
        """JUDGMENT: 401 is the package convention for a credential failure."""
        if client_id != self._deps.config.client_id:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The client_id does not match this app.",
                field="client_id",
            )

    def _check_secret(self, presented: str | None) -> None:
        """The high-trust flow authenticates with the app secret in the body."""
        if not presented:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="client_secret is required for a code issued without a code_challenge.",
                field="client_secret",
            )
        if presented != self._deps.config.client_secret:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The client_secret is incorrect.",
                field="client_secret",
            )

    def _check_verifier(self, verifier: str | None, challenge: str) -> None:
        """S256 only: ``BASE64URL(SHA256(ASCII(code_verifier)))`` unpadded (RFC 7636)."""
        if not verifier:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="code_verifier is required for a code issued with a code_challenge.",
                field="code_verifier",
            )
        computed = b64url_encode(hashlib.sha256(verifier.encode("utf-8")).digest())
        if computed != challenge:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="code_verifier does not match the code_challenge from the authorization request.",
                field="code_verifier",
            )

    def _mint(
        self,
        ctx: UnitContext,
        *,
        client_id: str,
        merchant_id: str,
        now_ms: int,
        operation_id: str,
    ) -> dict[str, Any]:
        """Issue and record one access/refresh pair, keyed to the caller's
        ``operation_id`` so a refresh journals as a refresh."""
        config = self._deps.config
        access_expires_ms = now_ms + config.access_token_ttl_ms
        refresh_expires_ms = now_ms + config.refresh_token_ttl_ms
        entity = TokenEntity(
            id=self._deps.ids.internal("tok"),
            access_token=self._deps.ids.access_token(),
            refresh_token=self._deps.ids.refresh_token(),
            client_id=client_id,
            merchant_id=merchant_id,
            access_token_expiration_ms=access_expires_ms,
            refresh_token_expiration_ms=refresh_expires_ms,
            # Every token inherits the app's fixed permission set (no per-token scope request).
            permissions=config.permissions,
            createdTime=now_ms,
        )
        ctx.store.collection(COL.tokens).insert(entity.to_entity(), {"operation_id": operation_id})
        return TokenResponse(
            access_token=entity.access_token,
            access_token_expiration=wire_seconds(access_expires_ms),
            refresh_token=entity.refresh_token,
            refresh_token_expiration=wire_seconds(refresh_expires_ms),
        ).wire()


# ---------------------------------------------------------------------------
# Module-level helpers: pure, and testable without a unit.
# ---------------------------------------------------------------------------


def oauth_routes(deps: CloverDeps) -> tuple[Route, ...]:
    """The OAuth routes for one vendor."""
    return CloverOAuthSurface(deps).routes()


def _query(args: HandlerArgs, name: str) -> str | None:
    """A query parameter: ``None`` if absent, the value if non-empty, a 400
    if present and empty (``?code_challenge=``)."""
    raw = args.query(name)
    if raw is None:
        return None
    if raw.strip() == "":
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{name} must not be empty.", field=name)
    return raw


def _failed_code(reason: str) -> UnitError:
    """The one documented phrase for every kind of bad code; the sidecar carries ``reason``."""
    return UnitError(
        UnitErrorKind.UNAUTHORIZED,
        detail=FAILED_CODE_MESSAGE,
        field="code",
        info={"reason": reason},
    )


def _the_merchant(ctx: UnitContext) -> MerchantEntity:
    """The merchant this unit represents: one per unit, the first
    insertion-ordered record."""
    merchants = ctx.store.collection(COL.merchants).all()
    if not merchants:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail="This unit has no merchant; OAuth cannot mint a code. The seed scenario arrives in PR E.",
        )
    return MerchantEntity.from_entity(merchants[0])


def _with_query(url: str, params: Mapping[str, str]) -> str:
    """``url`` with ``params`` set on its query string, replacing any existing
    occurrence of each key and preserving the rest."""
    parts = urlsplit(url)
    kept = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True) if name not in params]
    query = urlencode([*kept, *params.items()])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
