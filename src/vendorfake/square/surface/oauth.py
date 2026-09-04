"""The OAuth surface: authorize, obtain, refresh, revoke, status. Reproduces Square's four OAuth
endpoints with their documented token lifetimes and scope-narrowing rules.
https://developer.squareup.com/reference/square/oauth-api/authorize https://developer.squareup.com/reference/square/o-auth-api/retrieve-token-status

DOCUMENTED -- codes expire after 5 minutes, are single use, and a denial redirects with
``error=access_denied&error_description=user_denied``.
https://developer.squareup.com/docs/oauth-api/receive-and-manage-tokens
DOCUMENTED -- ``short_lived: true`` expires the access token in 24 hours, otherwise 30 days; code-flow
refresh returns the same refresh token, PKCE refresh a new one expiring after 90 days.
https://developer.squareup.com/reference/square/oauth-api/obtain-token https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope
DOCUMENTED -- a requested ``scopes`` list only narrows the grant to its intersection with what the
refresh token already carries (:func:`_narrowed_scopes`).
DOCUMENTED -- revoke returns ``{"success": true}`` and revokes every token for the merchant unless
``revoke_only_access_token`` is set. https://developer.squareup.com/reference/square/oauth-api/revoke-token
JUDGMENT -- Square publishes no error table for ``/oauth2/token`` or ``/oauth2/revoke``; failures use
the standard v2 envelope. JUDGMENT -- there is no consent screen to click, so ``authorize`` approves
automatically; ``unit_prompt=deny``/``unit_prompt=html`` simulate a denial or a human-driven page.
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
"""Square's ``scope`` parameter splits on whitespace or a literal ``+``."""

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

        # Absence (not the resolved value) is what the code entity carries.
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

        # Narrow before the write: a refused exchange must not burn the code.
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

        # Intersect against what the seller approved, not what this token carries; `is None`, not
        # truthiness (roadmap#28).
        approved = existing.scopes if existing.authorized_scopes is None else existing.authorized_scopes
        narrowed = _narrowed_scopes(grant.scopes, approved)

        now = ctx.clock.iso_ms()
        if existing.flow == "pkce":
            # PKCE refresh tokens are single-use: retire the old record, journalled.
            def retire(draft: Entity) -> None:
                draft["revoked_at"] = now

            tokens.update(existing.id, retire, meta={"operation_id": "ObtainToken", "grant": "refresh_token"})
        else:
            # Code flow: the prior access token stays valid
            # (https://developer.squareup.com/docs/oauth-api/overview). This silent write keeps the
            # refresh lookup single-valued.
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
        """Revoke one access token, or the merchant's whole authorization. DOCUMENTED -- ``success``
        is only ``true`` when a revocation actually happened.
        https://developer.squareup.com/reference/square/oauth-api/revoke-token
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
                # Nothing to revoke is not success; already-revoked records still count as victims.
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
        # Read defensively: None here would otherwise surface as a `not_found` naming an empty id.
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
        """PKCE authenticates via the verifier: S256 only, ``BASE64URL(SHA256(ASCII(code_verifier)))``, unpadded."""
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
        """DOCUMENTED -- "Required if provided in the authorization URL."
        https://developer.squareup.com/reference/square/oauth-api/obtain-token
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
    """DOCUMENTED -- ``scopes`` narrows the grant to its intersection with ``authorized`` and can
    never widen it; absent, it grants the whole authorized set.
    https://developer.squareup.com/reference/square/oauth-api/obtain-token https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope
    JUDGMENT -- an empty intersection is refused rather than minting a token that 403s on every call.
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
    """The record a refresh may be minted from. ``superseded_at`` is filtered inside the predicate,
    not after: ``Collection.find`` answers the first match, and two code-flow records can share one
    refresh-token string."""

    def predicate(entity: Entity) -> bool:
        return entity.get("refresh_token") == refresh_token and entity.get("superseded_at") is None

    return predicate


def _first_merchant(ctx: UnitContext) -> MerchantEntity:
    """The seller this unit represents (one per unit; the seed inserts exactly one)."""
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
    """``url`` with ``params`` set on its query string, ``None`` values dropped and an existing
    occurrence of a key replaced rather than appended."""
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
    """JUDGMENT -- a two-link consent page standing in for Square's real interactive screen; every
    interpolated value is escaped since the merchant name and scopes are consumer-controlled."""
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
