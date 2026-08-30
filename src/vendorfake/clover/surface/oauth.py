"""The OAuth v2 surface: authorize, token exchange, refresh.

FOR: reproducing the three endpoints a Clover OAuth consumer drives, with the
documented redirect parameters, the documented four-field token response and
the documented single-use refresh rotation -- so that an integration which
handles rotation correctly against this unit handles it correctly against
Clover, and one which does not fails here first.

=========  =================================================================
Authorize  ``GET  /oauth/v2/authorize``
           https://docs.clover.com/dev/docs/high-trust-app-auth-flow
Token      ``POST /oauth/v2/token``
           https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token
Refresh    ``POST /oauth/v2/refresh``
           https://docs.clover.com/dev/docs/refresh-access-tokens
=========  =================================================================

Documented behaviour reproduced here
------------------------------------
* the authorize redirect carries ``merchant_id``, ``client_id`` and ``code``
  -- Clover, unlike Square, identifies the merchant and echoes the app in the
  callback (high-trust-app-auth-flow, verbatim shape);
* token exchange accepts ``{client_id, client_secret, code}`` (high-trust) or
  ``{client_id, code, code_verifier}`` (PKCE) and answers exactly
  ``{access_token, access_token_expiration, refresh_token,
  refresh_token_expiration}``, expirations in Unix **seconds**;
* refresh takes ``{client_id, refresh_token}`` with **no client_secret**, and
  "Refresh token is for single use and becomes invalid immediately after a
  new access_token and refresh_token pair is generated" -- reusing a rotated
  refresh token is refused;
* access tokens live 30 minutes ("OAuth access_tokens expire in 30 minutes",
  oauth-and-tokens-faqs).

JUDGMENT, each labelled at its site
-----------------------------------
* **Error bodies.** Clover documents no OAuth error body, status or code
  anywhere (audit gap 2). Failures answer the package envelope
  (``{"message": ...}``) with 401 for credential failures and 400 for
  malformed requests; the bad-code message is the one phrase Clover's own FAQ
  uses, "Failed to validate authentication code"
  (https://docs.clover.com/dev/docs/oauth-and-tokens-faqs), answered
  identically for an unknown, spent or expired code -- a real authorization
  server does not say which, and the ``unit_error`` sidecar carries the
  distinction for whoever is debugging.
* **Code TTL and single use** -- documented nowhere; ten minutes and
  single-use per RFC 6749 convention, from ``CloverConfig``.
* **``state``** -- Clover's v2 authorize example shows no ``state`` parameter.
  It is passed through to the redirect when the consumer sends one, because
  every standard OAuth client library sends and verifies it and a fake that
  dropped it would break CSRF checks the consumer is right to run. A consumer
  must not conclude from this unit that Clover itself echoes it.
* **PKCE at authorize** -- the *token* side of PKCE is documented
  (``code_verifier`` in the exchange body); the authorize-side
  ``code_challenge`` parameter is not shown on the v2 pages, so accepting it
  here follows RFC 7636, S256 only -- and an omitted ``code_challenge_method``
  is refused rather than defaulted, because the RFC's default is ``plain``.
  A code issued with a challenge demands the verifier at exchange; one issued
  without demands the secret.
* **Prior access tokens survive a refresh.** Clover's docs invalidate only
  the *refresh* token on rotation and say nothing about access tokens minted
  earlier, so this unit keeps them valid to their own expiry. Inventing
  revocation would teach consumers an invalidation rule Clover does not
  publish, and they would discover its absence in production.

THE ORDERING INVARIANT (the N-1 class from the Square build): **no 4xx leaves
a journal entry.** Every refusal on the exchange path -- unknown client, bad
code, expired code, secret, verifier -- is computed before the code's
mark-used write, and every refusal on the refresh path before the rotation
write. A refused request must leave the world exactly as it found it, so the
consumer's corrected retry with the same code or refresh token succeeds.
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
"""Clover's own phrase for a code it will not accept, from the OAuth FAQ
(https://docs.clover.com/dev/docs/oauth-and-tokens-faqs). Used verbatim for an
unknown, spent and expired code alike; see the module docstring."""


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
        """The consent redirect, approval automatic (a mock has nobody to click).

        The redirect query is the documented shape:
        ``?merchant_id=...&client_id=...&code=...``
        (https://docs.clover.com/dev/docs/high-trust-app-auth-flow).
        """
        config = self._deps.config
        client_id = args.query("client_id")
        if not client_id:
            raise UnitError(UnitErrorKind.MISSING_FIELD, detail="client_id is required.", field="client_id")
        if client_id != config.client_id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Unknown client_id {client_id!r}. This unit is configured for app {config.client_id!r}.",
                field="client_id",
            )

        supplied_redirect = args.query("redirect_uri")
        if supplied_redirect and supplied_redirect != config.redirect_uri:
            # The mismatch this parameter exists to catch. JUDGMENT on the
            # status -- Clover documents no error for it -- but not on the
            # refusal: redirecting a code to an unregistered URI is the attack.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="redirect_uri does not match the redirect URI registered for this app.",
                field="redirect_uri",
            )
        redirect_uri = supplied_redirect or config.redirect_uri

        challenge = args.query("code_challenge")
        method = args.query("code_challenge_method")
        if challenge and method != "S256":
            # JUDGMENT: an omitted method is refused, not defaulted to S256.
            # RFC 7636 s4.3 makes `plain` the default when the method is
            # absent, and this unit refuses `plain`; silently upgrading an
            # absent method to S256 would accept a request the RFC reads as
            # the one method we reject. Clover documents nothing either way.
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
            # JUDGMENT: not in Clover's documented redirect; see the module
            # docstring for why it is echoed anyway.
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
        if record.used_at_ms is not None:
            raise _failed_code("already_used")
        if is_past_ms(record.expires_at_ms, ctx.clock):
            raise _failed_code("expired")

        # Every refusal above and below this comment happens BEFORE the
        # mark-used write: a refused exchange must not burn the code (the
        # module docstring's ordering invariant).
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
            # Unreachable while one app is configured (`_check_client` already
            # matched the request to it), but a second seeded app must not be
            # able to rotate the first app's grant. Same 401 as any other
            # credential failure, and above every write.
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The refresh token was not issued to this client_id.",
                field="refresh_token",
            )
        if existing.refresh_used_at_ms is not None:
            # "Refresh token is for single use and becomes invalid immediately
            # after a new access_token and refresh_token pair is generated."
            # https://docs.clover.com/dev/docs/refresh-access-tokens
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

        # All refusals are above; the rotation write is below. A refused
        # refresh must not retire the token -- with single-use rotation that
        # would lock the consumer out of the grant permanently, answered by a
        # 4xx claiming nothing happened.
        now_ms = int(ctx.clock.now())

        def rotate(draft: Entity) -> None:
            draft["refresh_used_at_ms"] = now_ms

        # Journalled: rotation is a real, documented state change a consumer
        # can observe (the old refresh token stops working). The old ACCESS
        # token is deliberately untouched; see the module docstring.
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
        """JUDGMENT on the status: no OAuth error is documented, and 401 is
        the package convention for a credential failure."""
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
        """PKCE proves knowledge of the verifier: S256 only,
        ``BASE64URL(SHA256(ASCII(code_verifier)))`` unpadded (RFC 7636)."""
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
        """Issue one access/refresh pair, record it, and answer the documented
        four fields. Stored instants are ms; the wire gets Unix seconds
        through :func:`~vendorfake.clover.surface.common.wire_seconds`.

        ``operation_id`` is the caller's, so a refresh journals the token it
        minted as a refresh: one request, one operation, however many writes.
        """
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
            # Every token inherits the app's fixed permission set; Clover has
            # no per-token scope request. See config.py.
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


def _failed_code(reason: str) -> UnitError:
    """The one documented phrase for every kind of bad code; the sidecar says
    which kind, for whoever is debugging. See the module docstring."""
    return UnitError(
        UnitErrorKind.UNAUTHORIZED,
        detail=FAILED_CODE_MESSAGE,
        field="code",
        info={"reason": reason},
    )


def _the_merchant(ctx: UnitContext) -> MerchantEntity:
    """The merchant this unit represents.

    One per unit; the first insertion-ordered record is the answer. Until the
    PR-E seed ships one, a unit doing OAuth needs a merchant inserted by its
    driver, and the error says so.
    """
    merchants = ctx.store.collection(COL.merchants).all()
    if not merchants:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail="This unit has no merchant; OAuth cannot mint a code. The seed scenario arrives in PR E.",
        )
    return MerchantEntity.from_entity(merchants[0])


def _with_query(url: str, params: Mapping[str, str]) -> str:
    """``url`` with ``params`` set on its query string.

    Built with :func:`urllib.parse.urlencode` rather than by concatenation, so
    a redirect URI that already carries a query keeps it and a ``state`` value
    containing ``&`` survives the round trip. Existing occurrences of a key
    are replaced rather than appended.
    """
    parts = urlsplit(url)
    kept = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True) if name not in params]
    query = urlencode([*kept, *params.items()])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
