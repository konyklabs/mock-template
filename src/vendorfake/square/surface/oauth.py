"""The OAuth surface: authorize, obtain, refresh, revoke, status.

FOR: reproducing the four endpoints an OAuth consumer drives, with the
documented lifetimes, the documented redirect parameters and the documented
difference between the code flow and PKCE -- so that an integration which
handles refresh correctly against this unit handles it correctly against
Square, and one which does not fails here first.

======================  ============================================================
Authorize               ``GET  /oauth2/authorize``
                        https://developer.squareup.com/reference/square/oauth-api/authorize
ObtainToken             ``POST /oauth2/token``
                        https://developer.squareup.com/reference/square/oauth-api/obtain-token
RevokeToken             ``POST /oauth2/revoke``
                        https://developer.squareup.com/reference/square/oauth-api/revoke-token
RetrieveTokenStatus     ``POST /oauth2/token/status``
                        https://developer.squareup.com/reference/square/o-auth-api/retrieve-token-status
======================  ============================================================

Documented behaviour reproduced here
------------------------------------
* authorization codes expire after 5 minutes and are single use, and the
  redirect carries ``code``, ``response_type=code`` and ``state``
  (https://developer.squareup.com/docs/oauth-api/receive-and-manage-tokens);
* a denial redirects with ``error=access_denied&error_description=user_denied``
  (same page);
* ``short_lived: true`` expires the access token in 24 hours, otherwise 30 days
  (https://developer.squareup.com/reference/square/oauth-api/obtain-token);
* code-flow refresh returns the SAME refresh token and PKCE refresh returns a
  new single-use one that expires after 90 days
  (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope);
* a requested ``scopes`` list narrows the grant and never widens it: the token
  carries "the intersection of these requested permissions and those authorized
  by the provided ``refresh_token``"
  (https://developer.squareup.com/reference/square/oauth-api/obtain-token);
* ``redirect_uri`` is "Required if provided in the authorization URL", and is
  checked against the one the code was issued for;
* revoke takes ``Authorization: Client {APPLICATION_SECRET}``, returns
  ``{"success": true}``, and revokes every token for the merchant unless
  ``revoke_only_access_token`` is set
  (https://developer.squareup.com/reference/square/oauth-api/revoke-token).

Three corrections to the TypeScript reference this file was rebuilt from
-----------------------------------------------------------------------
**``scopes`` narrows a grant and can never widen it.** The reference replaced
the granted set with whatever the request asked for -- on both the exchange and
the refresh path -- so a token could be minted with permissions the seller
never approved. Square documents the opposite in one sentence: "The returned
access token is limited to the permissions that are the intersection of these
requested permissions and those authorized by the provided ``refresh_token``"
(https://developer.squareup.com/reference/square/oauth-api/obtain-token). The
escalation is the one defect here with a security shape: a consumer whose test
asks "does my down-scoped token really lose write access?" passed against the
reference and failed against Square, and one that over-requests by accident got
a broader token from the fake than the API would ever issue. See
:func:`_narrowed_scopes`.

**Code-flow refresh no longer revokes the previous access token.** The
reference set ``revokedAt`` on the old record on every refresh, for both flows.
Square documents the opposite for the code flow -- "A refresh token obtained
using the code flow can be used to get multiple active access tokens"
(https://developer.squareup.com/docs/oauth-api/overview) -- and the
refresh/revoke page contains no sentence invalidating the prior access token. A
fake that revokes teaches consumers a token-invalidation rule Square does not
have, and the consumer only discovers it in production, where the rule is
absent and their re-auth path never runs. PKCE keeps the old behaviour, because
a PKCE refresh token really is single use.

Dropping the revoke leaves two live records sharing one refresh-token string,
since the code flow returns the same string. ``Collection.find`` answers in
insertion order, so the *stale* record would be found on the second refresh and
the new token would be minted from its scopes and its flow. The older record is
therefore marked ``superseded_at`` by a **silent** update -- no version bump, no
journal entry, and therefore no webhook -- and :func:`_find_refreshable` filters
on it. The older *access* token stays valid until its own expiry, which is the
documented behaviour; only the refresh lookup narrows.

**``redirect_uri`` is validated.** The reference stored it on the code entity
and never looked at it again, so a consumer testing a redirect-URI mismatch got
a false pass -- the one test they wrote for the attack the parameter exists to
prevent. It is recorded on the code only when the authorization request
actually supplied it, which is exactly the condition Square's "Required if
provided in the authorization URL" is keyed on, and absence stays absence.

JUDGMENT -- error statuses on these endpoints are this unit's convention.
Square publishes NO error table, status codes or example error bodies for
``/oauth2/token`` or ``/oauth2/revoke``; the ObtainToken response schema lists
an ``errors`` array and nothing more. The failures below use the standard v2
envelope with ``AUTHENTICATION_ERROR``/``UNAUTHORIZED`` for credential failures
and ``INVALID_REQUEST_ERROR`` for malformed ones. Treat them as this mock's
convention, not as Square fidelity.

JUDGMENT -- form-encoded request bodies are accepted here. Square documents
``POST /oauth2/token`` as ``Content-Type: application/json``, with a verbatim
curl example, and publishes nothing about ``application/x-www-form-urlencoded``.
Accepting both is a call made in the consumer's favour: the OAuth endpoints are
the ones people reach with a form-encoded client out of habit, and failing on
the content type rather than on the thing under test helps nobody. The
acceptance itself costs nothing here -- ``HandlerArgs.body()`` in the core is
content-type general -- but the *consequence* is real and is spelled out in
:mod:`vendorfake.square.model.oauth`: booleans coerce, so ``short_lived=true``
in a form body means what it says.

JUDGMENT -- the real authorize page is an interactive consent screen and a mock
has nobody to click it, so approval is automatic. ``unit_prompt=deny`` produces
the documented denial redirect and ``unit_prompt=html`` renders a two-link
consent page for a human driving the flow in a browser. ``unit_prompt`` is the
only non-Square parameter in this vendor's surface.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from vendorfake.core.kernel.reply import json_, redirect, text
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
from vendorfake.square.entities import COL, AuthorizationCodeEntity, MerchantEntity, TokenEntity
from vendorfake.square.model.common import validate_body
from vendorfake.square.model.oauth import (
    SUPPORTED_GRANT_TYPES,
    AuthorizationCodeGrant,
    ObtainTokenEnvelope,
    RefreshTokenGrant,
    RevokeTokenRequest,
    TokenResponse,
    TokenStatusResponse,
)
from vendorfake.square.surface.common import SquareDeps, is_expired

__all__ = ["CAPABILITY", "OAuthSurface", "oauth_routes"]

CAPABILITY = "oauth"
"""The capability every route below belongs to."""

_SCOPE_SEPARATORS = re.compile(r"[\s+]+")
"""How Square's ``scope`` parameter is split.

Whitespace *or* a literal ``+``: a consumer that hand-builds the authorize URL
often percent-decodes to ``ORDERS_READ+ORDERS_WRITE`` and one that uses a URL
builder produces spaces. Both mean the same list.
"""

_PROMPT_DENY = "deny"
_PROMPT_HTML = "html"


class OAuthSurface:
    """The four OAuth routes, bound to one vendor's config and id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/oauth2/authorize",
                capability=CAPABILITY,
                handler=self.authorize,
                operation_id="Authorize",
                summary="Authorization page. Redirects back with an authorization code.",
            ),
            Route(
                method="POST",
                path="/oauth2/token",
                capability=CAPABILITY,
                handler=self.obtain_token,
                operation_id="ObtainToken",
                summary="Exchange an authorization code, or refresh an access token.",
            ),
            Route(
                method="POST",
                path="/oauth2/revoke",
                capability=CAPABILITY,
                handler=self.revoke_token,
                auth="client-secret",
                operation_id="RevokeToken",
                summary="Revoke an access token, or every token held for a merchant.",
            ),
            Route(
                method="POST",
                path="/oauth2/token/status",
                capability=CAPABILITY,
                handler=self.token_status,
                auth="bearer",
                operation_id="RetrieveTokenStatus",
                summary="Scopes and expiry for the bearer token presented.",
            ),
        )

    # -- GET /oauth2/authorize ---------------------------------------------

    def authorize(self, args: HandlerArgs) -> ReplyInit:
        config = self._deps.config
        client_id = args.query("client_id")
        if not client_id:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="client_id is required.",
                field="client_id",
            )
        if client_id != config.application_id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"Unknown client_id {client_id!r}. "
                    f"This unit is configured for application {config.application_id!r}."
                ),
                field="client_id",
            )

        # Absence is the thing recorded, not the fallback: Square requires
        # `redirect_uri` at token exchange only when the authorization request
        # supplied one, so `supplied` -- not the resolved value -- is what the
        # code entity carries.
        supplied_redirect = args.query("redirect_uri")
        redirect_uri = supplied_redirect or config.redirect_uri
        if not redirect_uri:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="redirect_uri was not supplied and the unit has no configured redirect URL.",
                field="redirect_uri",
            )

        state = args.query("state")
        prompt = args.query("unit_prompt")

        if prompt == _PROMPT_DENY:
            return redirect(
                _with_query(
                    redirect_uri,
                    {"error": "access_denied", "error_description": "user_denied", "state": state},
                )
            )

        scopes = _split_scopes(args.query("scope") or " ".join(config.default_scopes))
        merchant = _first_merchant(args.ctx)

        if prompt == _PROMPT_HTML:
            return _consent_page(merchant, client_id, redirect_uri, scopes, state)

        code = self._deps.ids.authorization_code()
        args.ctx.store.collection(COL.codes).insert(
            AuthorizationCodeEntity(
                id=code,
                client_id=client_id,
                merchant_id=merchant.id,
                scopes=scopes,
                redirect_uri=supplied_redirect,
                code_challenge=args.query("code_challenge"),
                expires_at=args.ctx.clock.iso_seconds(config.authorization_code_ttl_ms),
            ).to_entity(),
            {"operation_id": "Authorize"},
        )
        return redirect(_with_query(redirect_uri, {"code": code, "response_type": "code", "state": state}))

    # -- POST /oauth2/token -------------------------------------------------

    def obtain_token(self, args: HandlerArgs) -> ReplyInit:
        body = args.body()
        envelope = validate_body(ObtainTokenEnvelope, body)
        if envelope.client_id != self._deps.config.application_id:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The `client_id` does not match this application.",
                field="client_id",
            )
        if envelope.grant_type == "authorization_code":
            return json_(self._exchange_code(args.ctx, body))
        if envelope.grant_type == "refresh_token":
            return json_(self._refresh_token(args.ctx, body))
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"grant_type {envelope.grant_type!r} is not supported. Supported: {', '.join(SUPPORTED_GRANT_TYPES)}."
            ),
            field="grant_type",
            info={"supported": list(SUPPORTED_GRANT_TYPES)},
        )

    def _exchange_code(self, ctx: UnitContext, body: Mapping[str, Any]) -> dict[str, Any]:
        grant = validate_body(AuthorizationCodeGrant, body)
        codes = ctx.store.collection(COL.codes)
        stored = codes.get(grant.code)
        if stored is None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The authorization code is invalid.",
                field="code",
            )
        record = AuthorizationCodeEntity.from_entity(stored)
        if record.used_at is not None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The authorization code has already been used. Codes are single use.",
                field="code",
            )
        if is_expired(record.expires_at, ctx.clock):
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The authorization code expired. Codes expire 5 minutes after they are issued.",
                field="code",
            )

        flow: Literal["code", "pkce"] = "pkce" if record.code_challenge else "code"
        if flow == "pkce":
            self._check_verifier(grant, record)
        else:
            self._check_secret(grant.client_secret)
        self._check_redirect_uri(grant.redirect_uri, record.redirect_uri)

        # Narrow BEFORE the write, not as an argument to `_mint`.
        #
        # `_narrowed_scopes` raises when the intersection is empty, and Python
        # evaluates arguments after the statements above have already run. With
        # the call left inline, a refused exchange still burned the code: the
        # consumer got a 400 saying nothing happened, and their next correct
        # attempt got a 401 because the code was spent. Every other refusal on
        # this endpoint is ordered before the write; this one has to be too.
        # The rule it belongs to is broader than OAuth -- a 4xx must not leave a
        # journal entry behind -- and the conformance suite now asserts it.
        narrowed = _narrowed_scopes(grant.scopes, record.scopes)

        def mark_used(draft: Entity) -> None:
            draft["used_at"] = ctx.clock.iso_ms()

        codes.update(record.id, mark_used, meta={"operation_id": "ObtainToken"})

        return self._mint(
            ctx,
            client_id=record.client_id,
            merchant_id=record.merchant_id,
            scopes=narrowed,
            authorized_scopes=record.scopes,
            short_lived=grant.short_lived,
            flow=flow,
            refresh_token=self._deps.ids.refresh_token(),
        )

    def _refresh_token(self, ctx: UnitContext, body: Mapping[str, Any]) -> dict[str, Any]:
        grant = validate_body(RefreshTokenGrant, body)
        tokens = ctx.store.collection(COL.tokens)
        found = tokens.find(_live_holder_of(grant.refresh_token))
        if found is None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The refresh token is invalid.",
                field="refresh_token",
            )
        existing = TokenEntity.from_entity(found)
        if existing.revoked_at is not None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The refresh token was revoked.",
                field="refresh_token",
            )
        if existing.flow == "pkce":
            if existing.refresh_token_expires_at is not None and is_expired(
                existing.refresh_token_expires_at, ctx.clock
            ):
                raise UnitError(
                    UnitErrorKind.UNAUTHORIZED,
                    detail="The refresh token expired. PKCE refresh tokens expire after 90 days.",
                    field="refresh_token",
                )
        else:
            self._check_secret(grant.client_secret)

        # Narrow before the retire/supersede write below, for the reason given
        # on the exchange path: a PKCE refresh token is single-use, so a refused
        # request that had already retired it would lock the consumer out of the
        # grant permanently -- with a 400 telling them nothing had happened.
        # Intersect against what the SELLER APPROVED, not against what this
        # token happens to carry. Square narrows "from the ones granted when the
        # seller approved", so a refresh asking for a permission that an earlier
        # down-scoped refresh dropped must still succeed.
        #
        # Using existing.scopes made every narrowing permanent -- a ratchet with
        # no way back, and the mirror image of the escalation this surface was
        # fixed for. Wrong in the other direction, and refused rather than
        # over-granted, which is why it reads as safe and is not.
        # `is None`, not truthiness: None is "not recorded" (a seeded token)
        # and falls back; an empty RECORDED approval must intersect as empty
        # and be refused, not silently re-grant the token's own scopes
        # (konyklabs/roadmap#28).
        approved = existing.scopes if existing.authorized_scopes is None else existing.authorized_scopes
        narrowed = _narrowed_scopes(grant.scopes, approved)

        now = ctx.clock.iso_ms()
        if existing.flow == "pkce":
            # "Refresh tokens obtained using the PKCE flow are single-use
            # tokens": the old record is retired, and this one is journalled
            # because it is a real state change a consumer can observe.
            def retire(draft: Entity) -> None:
                draft["revoked_at"] = now

            tokens.update(existing.id, retire, meta={"operation_id": "ObtainToken", "grant": "refresh_token"})
        else:
            # Code flow: the prior ACCESS token stays valid, so this is not a
            # revocation. It is a silent write -- no version bump, no journal
            # entry, no webhook -- whose only purpose is to keep the refresh
            # lookup single-valued now that two records share one refresh
            # token. See the module docstring.
            def supersede(draft: Entity) -> None:
                draft["superseded_at"] = now

            tokens.update(existing.id, supersede, silent=True)

        return self._mint(
            ctx,
            client_id=existing.client_id,
            merchant_id=existing.merchant_id,
            scopes=narrowed,
            authorized_scopes=approved,
            # Set on refresh, never cleared by it; see the model.
            short_lived=grant.short_lived or existing.short_lived,
            flow=existing.flow,
            refresh_token=(existing.refresh_token if existing.flow == "code" else self._deps.ids.refresh_token()),
        )

    # -- POST /oauth2/revoke ------------------------------------------------

    def revoke_token(self, args: HandlerArgs) -> ReplyInit:
        """Revoke one access token, or the merchant's whole authorization.

        ``success`` is documented as "If the request is successful, this is
        ``true``" and nothing else on the page describes a failure
        (https://developer.squareup.com/reference/square/oauth-api/revoke-token),
        so the field's only job is to say whether the revocation happened. It
        must therefore never be ``true`` over a request that revoked nothing:
        a ``client_id`` with a typo in it, or a merchant this application holds
        no token for, used to answer ``{"success": true}`` here while the token
        went on working -- a green test and a live credential.

        JUDGMENT -- the statuses. Square publishes no error table for this
        endpoint (see the module docstring), so the refusals below are this
        unit's convention: a ``client_id`` that is not this application is the
        same 401 :meth:`obtain_token` already answers, and a selector that
        matches no token this application issued is the 401 the
        ``access_token`` branch already answered.
        """
        request = validate_body(RevokeTokenRequest, args.body())
        if request.client_id != self._deps.config.application_id:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The `client_id` does not match this application.",
                field="client_id",
            )
        if not request.access_token and not request.merchant_id:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="Provide either access_token or merchant_id.",
                field="access_token",
            )
        if request.access_token and request.merchant_id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Do not provide access_token together with merchant_id.",
                field="merchant_id",
            )

        tokens = args.ctx.store.collection(COL.tokens)
        target: TokenEntity | None = None
        if request.access_token:
            match = tokens.find(lambda entity: entity.get("access_token") == request.access_token)
            if match is None:
                raise UnitError(
                    UnitErrorKind.UNAUTHORIZED,
                    detail="The provided access token was not issued by this application.",
                )
            target = TokenEntity.from_entity(match)

        merchant_id = request.merchant_id or (target.merchant_id if target is not None else "")
        if request.revoke_only_access_token and target is not None:
            victims = [target]
        else:
            victims = [
                TokenEntity.from_entity(entity)
                for entity in tokens.filter(
                    lambda entity: (
                        entity.get("merchant_id") == merchant_id and entity.get("client_id") == request.client_id
                    )
                )
            ]
            if not victims:
                # Nothing to revoke is not a successful revocation; see the
                # docstring. Already-revoked records still count as victims, so
                # a retried revocation stays idempotent.
                raise UnitError(
                    UnitErrorKind.UNAUTHORIZED,
                    detail=f"This application holds no token for merchant {merchant_id}.",
                    field="merchant_id",
                )

        at = args.ctx.clock.iso_ms()

        def revoke(draft: Entity) -> None:
            draft["revoked_at"] = at

        for victim in victims:
            if victim.revoked_at is not None:
                continue
            tokens.update(victim.id, revoke, meta={"operation_id": "RevokeToken"})
        return json_({"success": True})

    # -- POST /oauth2/token/status ------------------------------------------

    def token_status(self, args: HandlerArgs) -> ReplyInit:
        # `auth` is set because the route declares a mode, and `token_id` is
        # set because the bearer scheme resolves to a stored token. Both are
        # read defensively anyway: a None here would otherwise surface as a
        # `not_found` naming an empty id.
        token_id = args.auth.token_id if args.auth is not None else None
        if token_id is None:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail="The bearer credential resolved without a token id.",
            )
        token = TokenEntity.from_entity(args.ctx.store.collection(COL.tokens).require(token_id))
        return json_(
            TokenStatusResponse(
                scopes=list(token.scopes),
                expires_at=token.expires_at,
                client_id=token.client_id,
                merchant_id=token.merchant_id,
            ).wire()
        )

    # -- shared ------------------------------------------------------------

    def _check_secret(self, presented: str | None) -> None:
        """The code flow authenticates with the application secret."""
        if not presented:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="client_secret is required.",
                field="client_secret",
            )
        if presented != self._deps.config.application_secret:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The client_secret is incorrect.",
                field="client_secret",
            )

    def _check_verifier(self, grant: AuthorizationCodeGrant, record: AuthorizationCodeEntity) -> None:
        """PKCE authenticates by proving knowledge of the verifier instead.

        S256 only: ``BASE64URL(SHA256(ASCII(code_verifier)))``, unpadded, which
        is what Node's ``digest('base64url')`` produces and what every PKCE
        client sends.
        """
        if not grant.code_verifier:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="code_verifier is required for a code issued with a code_challenge.",
                field="code_verifier",
            )
        challenge = b64url_encode(hashlib.sha256(grant.code_verifier.encode("utf-8")).digest())
        if challenge != record.code_challenge:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="code_verifier does not match the code_challenge from the authorization request.",
                field="code_verifier",
            )

    def _check_redirect_uri(self, presented: str | None, issued_for: str | None) -> None:
        """ "Required if provided in the authorization URL."

        https://developer.squareup.com/reference/square/oauth-api/obtain-token

        ``issued_for`` is ``None`` when the authorization request named no
        redirect URI and the unit's configured default was used, in which case
        Square asks for nothing and neither does this. A value supplied at
        token exchange when none was supplied at authorize time is ignored,
        which is also where Square's sentence stops.
        """
        if issued_for is None:
            return
        if not presented:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="redirect_uri is required because one was supplied in the authorization request.",
                field="redirect_uri",
            )
        if presented != issued_for:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="redirect_uri does not match the one supplied in the authorization request.",
                field="redirect_uri",
            )

    def _mint(
        self,
        ctx: UnitContext,
        *,
        client_id: str,
        merchant_id: str,
        scopes: tuple[str, ...],
        authorized_scopes: tuple[str, ...],
        short_lived: bool,
        flow: Literal["code", "pkce"],
        refresh_token: str,
    ) -> dict[str, Any]:
        """Issue one access token and record it."""
        config = self._deps.config
        ttl_ms = config.short_lived_ttl_ms if short_lived else config.access_token_ttl_ms
        access_token = self._deps.ids.access_token()
        expires_at = ctx.clock.iso_seconds(ttl_ms)
        refresh_expires_at = ctx.clock.iso_seconds(config.pkce_refresh_ttl_ms) if flow == "pkce" else None

        entity = TokenEntity(
            id=self._deps.ids.internal("tok"),
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
            merchant_id=merchant_id,
            expires_at=expires_at,
            scopes=scopes,
            authorized_scopes=authorized_scopes,
            refresh_token_expires_at=refresh_expires_at,
            short_lived=short_lived,
            flow=flow,
        )
        ctx.store.collection(COL.tokens).insert(entity.to_entity(), {"operation_id": "ObtainToken"})
        return TokenResponse(
            access_token=access_token,
            expires_at=expires_at,
            merchant_id=merchant_id,
            refresh_token=refresh_token,
            short_lived=short_lived,
            refresh_token_expires_at=refresh_expires_at,
        ).wire()


# ---------------------------------------------------------------------------
# Module-level helpers: pure, and testable without a unit.
# ---------------------------------------------------------------------------


def oauth_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The OAuth routes for one vendor."""
    return OAuthSurface(deps).routes()


def _narrowed_scopes(requested: Sequence[str] | None, authorized: tuple[str, ...]) -> tuple[str, ...]:
    """``scopes`` NARROWS the grant. It can never widen it.

    "The returned access token is limited to the permissions that are the
    intersection of these requested permissions and those authorized by the
    provided `refresh_token`."
    https://developer.squareup.com/reference/square/oauth-api/obtain-token

    That sentence is the whole of the down-scoping story -- "you can create a
    new access token that has a reduced set of permissions from the ones
    granted when the seller approved your authorization"
    (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope)
    -- and nothing on either page permits the other direction. The intersection
    is applied on the code-exchange path too, against the scopes the seller
    approved at authorize time: the authorization code is what carries the
    approval there, so the same rule with the same authority behind it.

    Absent ``scopes`` is not an empty intersection: it means "no opinion", and
    the whole authorized set is granted. The order returned is the *requested*
    order, so a caller that asks for two permissions gets them back the way it
    asked for them.

    JUDGMENT -- an intersection with nothing in it is refused. Square documents
    no error table for this endpoint at all (see the module docstring), so
    there is no published answer; minting a token with an empty scope list
    would answer 200 and then 403 every call made with it, and the request that
    is actually wrong is this one.
    """
    if requested is None:
        return authorized
    approved = set(authorized)
    narrowed = tuple(dict.fromkeys(scope for scope in requested if scope in approved))
    if not narrowed:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                "None of the requested scopes were authorized by this grant. The access token is limited to the "
                "intersection of the requested permissions and the permissions the seller approved."
            ),
            field="scopes",
            info={"requested": list(requested), "authorized": list(authorized)},
        )
    return narrowed


def _live_holder_of(refresh_token: str) -> Callable[[Entity], bool]:
    """The record a refresh may be minted from, if there is one.

    ``superseded_at`` is filtered inside the predicate rather than after the
    lookup, because ``Collection.find`` answers with the *first* insertion-order
    match and two code-flow records legitimately share one refresh-token string.
    Filtering afterwards would find the stale record and stop there.
    """

    def predicate(entity: Entity) -> bool:
        return entity.get("refresh_token") == refresh_token and entity.get("superseded_at") is None

    return predicate


def _first_merchant(ctx: UnitContext) -> MerchantEntity:
    """The seller this unit represents.

    One per unit in practice; the first is the answer rather than an arbitrary
    one because ``all()`` is insertion-ordered and the seed inserts exactly one.
    """
    merchants = ctx.store.collection(COL.merchants).all()
    if not merchants:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail="The seed scenario contains no merchant; OAuth cannot mint a token.",
        )
    return MerchantEntity.from_entity(merchants[0])


def _split_scopes(raw: str) -> tuple[str, ...]:
    """Square's space-separated ``scope`` parameter as a tuple."""
    return tuple(part for part in _SCOPE_SEPARATORS.split(raw) if part)


def _with_query(url: str, params: Mapping[str, str | None]) -> str:
    """``url`` with ``params`` set on its query string, ``None`` values dropped.

    Built with :func:`urllib.parse.urlencode` rather than by concatenation, so
    a redirect URI that already carries a query keeps it and a state value
    containing ``&`` survives the round trip. Existing occurrences of a key are
    replaced rather than appended, matching ``URLSearchParams.set``.
    """
    parts = urlsplit(url)
    replaced = {name: value for name, value in params.items() if value is not None}
    kept = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True) if name not in replaced]
    query = urlencode([*kept, *replaced.items()])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _consent_page(
    merchant: MerchantEntity,
    client_id: str,
    redirect_uri: str,
    scopes: tuple[str, ...],
    state: str | None,
) -> ReplyInit:
    """The ``unit_prompt=html`` consent screen.

    Not a Square document -- Square's real page is an interactive consent
    screen this unit cannot reproduce -- but a human driving the flow in a
    browser needs somewhere to click, and two links are the whole of it. Every
    interpolated value goes through :func:`html.escape`, because a merchant
    name and a scope list both arrive from configuration a consumer controls.
    """
    approve = _with_query(
        "/oauth2/authorize",
        {"client_id": client_id, "redirect_uri": redirect_uri, "scope": " ".join(scopes), "state": state},
    )
    deny = _with_query(approve, {"unit_prompt": _PROMPT_DENY})
    name = html.escape(merchant.business_name)
    items = "".join(f"<li>{html.escape(scope)}</li>" for scope in scopes)
    return text(
        f'<!doctype html><meta charset="utf-8"><title>Authorize {name}</title>'
        f"<h1>{name}</h1><p>Grant these permissions?</p><ul>{items}</ul>"
        f'<p><a href="{html.escape(approve)}">Allow</a> &middot; '
        f'<a href="{html.escape(deny)}">Deny</a></p>',
        200,
        {"content-type": "text/html; charset=utf-8"},
    )
