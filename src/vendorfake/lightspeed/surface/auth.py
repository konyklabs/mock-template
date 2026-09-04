"""The authentication surface: the token endpoint, and a stand-in for the
authorize redirect.

NEITHER ROUTE IS IN ``api-2026-07.yaml``. The specification carries only the
resource API; the token endpoint lives under a different version segment
(``/api/1.0/token``) and the authorize redirect on a different host entirely,
and both are documented only in prose on
https://x-series-api.lightspeedhq.com/docs/authorization. Every citation below
is to that page.

===================  =========================================================
Authorize            ``GET  /connect``      -- a STAND-IN; see below
Token                ``POST /api/1.0/token`` -- form-encoded, two grants
===================  =========================================================

DOCUMENTED behaviour reproduced here
------------------------------------
* the authorize URL is
  ``.../connect?response_type=code&client_id=...&redirect_uri=...&state=...&scope=...``;
* the exchange takes ``code``, ``client_id``, ``client_secret``,
  ``grant_type=authorization_code`` and ``redirect_uri``, and the refresh takes
  ``grant_type=refresh_token`` with the stored refresh token;
* the response carries exactly ``access_token``, ``token_type`` ("Bearer"),
  ``expires`` (a Unix timestamp), ``expires_in`` (seconds), ``refresh_token``,
  ``domain_prefix`` and ``scope``;
* **rotation, and both halves of it**: "Using a refresh token will revoke the
  access token that was returned with it" AND "You must save this new refresh
  token and use it the next time". So a refresh call retires the consumed
  refresh token *and* revokes the access token that came with it. A consumer
  who keeps using the old access token after refreshing fails here, which is
  the defect this endpoint exists to catch.

JUDGMENT, each labelled at its site
------------------------------------
* **``GET /connect`` is a stand-in.** The real page is on the fixed host
  ``secure.retail.lightspeed.app`` and is an interactive consent screen; a unit
  serves one origin and has nobody to click it, so this route sits at the
  documented path, approves automatically and redirects with the code. The
  summary says "Stand-in" so ``GET /__unit/routes`` and the generated reference
  page both say so too.
* **the code's ten-minute, single-use lifetime** -- carried from the
  roadmap#75 spike and not re-quoted by the deeper pass (``config.py``).
* **the status a spent or reused credential gets.** The page documents the
  rotation and never a status; 401 is what the rest of this document uses for
  a credential that does not check out.
* **``client_secret`` is required on the refresh call too** -- see
  ``model/auth.py``.
* **a form-encoded body is what the page shows, and JSON is accepted as well.**
  The core's ``HandlerArgs.body()`` is content-type general, so this costs
  nothing and fails a consumer on the thing under test rather than on a
  content type.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from vendorfake.core.kernel.reply import json_, redirect
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitContext, UnitError, UnitErrorKind
from vendorfake.lightspeed.auth import KIND_OAUTH
from vendorfake.lightspeed.entities import COL, AuthorizationCodeEntity, RefreshTokenEntity, TokenEntity
from vendorfake.lightspeed.model.auth import (
    GRANT_AUTHORIZATION_CODE,
    GRANT_REFRESH_TOKEN,
    SCOPE_SEPARATOR,
    SUPPORTED_GRANT_TYPES,
    AuthorizationCodeGrant,
    RefreshTokenGrant,
    TokenEnvelope,
    TokenResponseWire,
)
from vendorfake.lightspeed.model.common import validate_body
from vendorfake.lightspeed.paths import CONNECT, TOKEN_EXCHANGE
from vendorfake.lightspeed.surface.common import LightspeedDeps, is_past_ms

__all__ = ["CAPABILITY", "RESPONSE_TYPE", "STAND_IN", "LightspeedAuthSurface", "auth_routes"]

CAPABILITY = "auth"

STAND_IN = "Stand-in (the real authorize page is on secure.retail.lightspeed.app)"

RESPONSE_TYPE = "code"
"""The one ``response_type`` the documented authorize URL carries."""


class LightspeedAuthSurface:
    """The two auth routes, bound to one vendor's config and id streams."""

    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `auth` on either: one issues the credential and the other
        # exchanges it. No `example_body`: the conformance suite aims its
        # committed-mutation contracts at the first example route, and a token
        # is a mutation the webhook mapper rightly never announces -- closing a
        # register is that route (`surface/registers.py`).
        return (
            Route(
                method="GET",
                path=CONNECT,
                capability=CAPABILITY,
                handler=self.connect,
                operation_id="Connect",
                summary=(f"{STAND_IN}: approves and redirects to redirect_uri with a single-use code and the state."),
            ),
            Route(
                method="POST",
                path=TOKEN_EXCHANGE,
                capability=CAPABILITY,
                handler=self.token,
                operation_id="TokenExchange",
                summary=(
                    "Exchange an authorization code, or refresh: rotation retires the refresh token and "
                    "revokes the access token issued with it."
                ),
            ),
        )

    # -- GET /connect -------------------------------------------------------

    def connect(self, args: HandlerArgs) -> ReplyInit:
        config = self._deps.config
        self._deps.limiter.consume(args.ctx)
        client_id = args.query("client_id")
        if not client_id:
            raise UnitError(UnitErrorKind.MISSING_FIELD, detail="client_id is required.", field="client_id")
        if client_id != config.client_id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"Unknown client_id {client_id!r}. This unit is configured for application {config.client_id!r}."
                ),
                field="client_id",
            )
        response_type = args.query("response_type")
        if response_type is not None and response_type != RESPONSE_TYPE:
            # The documented URL carries response_type=code and the page names
            # no other value; JUDGMENT that anything else is a bad request
            # rather than silently treated as `code`.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"response_type must be {RESPONSE_TYPE!r}; the authorization flow documents no other value.",
                field="response_type",
                info={"supplied": response_type},
            )
        # Absence is what is recorded, not the fallback: the exchange only has
        # to match a redirect_uri when the authorization request supplied one.
        supplied_redirect = args.query("redirect_uri")
        redirect_uri = supplied_redirect or config.redirect_uri
        if not redirect_uri:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="redirect_uri was not supplied and the unit has no configured redirect URL.",
                field="redirect_uri",
            )
        state = args.query("state")
        scopes = _split_scopes(args.query("scope")) or tuple(config.scopes)
        unknown = [scope for scope in scopes if scope not in config.scopes]
        if unknown:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"scope names {unknown}, which this application does not carry.",
                field="scope",
                info={"granted": list(config.scopes)},
            )
        code = self._deps.credential_ids.authorization_code()
        args.ctx.store.collection(COL.auth_codes).insert(
            AuthorizationCodeEntity(
                id=code,
                client_id=client_id,
                scopes=scopes,
                expires_at_ms=int(args.ctx.clock.now()) + config.authorization_code_ttl_ms,
                redirect_uri=supplied_redirect,
                state=state,
            ).to_entity(),
            {"operation_id": "Connect"},
        )
        return redirect(_with_query(redirect_uri, {"code": code, "state": state}))

    # -- POST /api/1.0/token ------------------------------------------------

    def token(self, args: HandlerArgs) -> ReplyInit:
        self._deps.limiter.consume(args.ctx)
        body = args.body()
        envelope = validate_body(TokenEnvelope, body)
        if envelope.client_id != self._deps.config.client_id:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The client_id does not match this application.",
                field="client_id",
            )
        if envelope.grant_type == GRANT_AUTHORIZATION_CODE:
            return json_(self._exchange(args.ctx, body))
        if envelope.grant_type == GRANT_REFRESH_TOKEN:
            return json_(self._refresh(args.ctx, body))
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"grant_type {envelope.grant_type!r} is not supported. The authorization flow documents "
                f"{' and '.join(SUPPORTED_GRANT_TYPES)}."
            ),
            field="grant_type",
            info={"supported": list(SUPPORTED_GRANT_TYPES)},
        )

    def _exchange(self, ctx: UnitContext, body: Mapping[str, Any]) -> dict[str, Any]:
        grant = validate_body(AuthorizationCodeGrant, body)
        self._check_secret(grant.client_secret)
        codes = ctx.store.collection(COL.auth_codes)
        stored = codes.get(grant.code)
        if stored is None:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, detail="The authorization code is invalid.", field="code")
        record = AuthorizationCodeEntity.from_entity(stored)
        if record.used_at_ms is not None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The authorization code has already been used. Codes are single use.",
                field="code",
            )
        if is_past_ms(record.expires_at_ms, ctx.clock):
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The authorization code expired.",
                field="code",
            )
        if record.redirect_uri is not None and grant.redirect_uri != record.redirect_uri:
            # "redirect_uri" is one of the five documented exchange parameters
            # and the authorize URL carries it too; checking them against each
            # other is the whole reason the parameter exists.
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="redirect_uri does not match the one the authorization code was issued for.",
                field="redirect_uri",
            )
        # Every refusal is above this line; the mint and its journal entries
        # are below.
        now = int(ctx.clock.now())
        codes.update(
            grant.code, lambda draft: draft.__setitem__("used_at_ms", now), meta={"operation_id": "TokenExchange"}
        )
        return self._mint(ctx, client_id=grant.client_id, scopes=record.scopes, now=now)

    def _refresh(self, ctx: UnitContext, body: Mapping[str, Any]) -> dict[str, Any]:
        grant = validate_body(RefreshTokenGrant, body)
        self._check_secret(grant.client_secret)
        refresh_tokens = ctx.store.collection(COL.refresh_tokens)
        found = refresh_tokens.find(lambda entity: entity.get("refresh_token") == grant.refresh_token)
        if found is None:
            raise UnitError(UnitErrorKind.UNAUTHORIZED, detail="The refresh token is not valid.", field="refresh_token")
        record = RefreshTokenEntity.from_entity(found)
        if record.retired_at_ms is not None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail=(
                    "The refresh token has already been used. Each refresh returns a new refresh token and "
                    "retires the one it consumed."
                ),
                field="refresh_token",
                info={"reason": "refresh_token_reused"},
            )
        now = int(ctx.clock.now())
        # Rotation, both documented halves. The consumed refresh token is
        # retired, and the access token it was returned with is revoked --
        # "Using a refresh token will revoke the access token that was returned
        # with it".
        refresh_tokens.update(
            record.id,
            lambda draft: draft.__setitem__("retired_at_ms", now),
            meta={"operation_id": "TokenExchange"},
        )
        if record.access_token_id is not None and ctx.store.collection(COL.tokens).has(record.access_token_id):
            ctx.store.collection(COL.tokens).update(
                record.access_token_id,
                lambda draft: draft.__setitem__("revoked_at_ms", now),
                meta={"operation_id": "TokenExchange"},
            )
        return self._mint(ctx, client_id=grant.client_id, scopes=record.scopes, now=now)

    # -- minting ------------------------------------------------------------

    def _check_secret(self, supplied: str) -> None:
        if supplied != self._deps.config.client_secret:
            # One phrase for a wrong id and a wrong secret: naming which half
            # was wrong tells an attacker something the real endpoint does not.
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The client credentials are not valid.",
                info={"reason": "client_secret_mismatch"},
            )

    def _mint(self, ctx: UnitContext, *, client_id: str, scopes: Sequence[str], now: int) -> dict[str, Any]:
        """One access token and one refresh token, linked so the next refresh
        knows which access token to revoke."""
        config = self._deps.config
        ids = self._deps.credential_ids
        access_token = ids.access_token()
        refresh_token = ids.refresh_token()
        token_id = self._deps.ids.uuid()
        refresh_id = self._deps.ids.uuid()
        expires_at_ms = now + config.access_token_ttl_ms
        ctx.store.collection(COL.tokens).insert(
            TokenEntity(
                id=token_id,
                access_token=access_token,
                client_id=client_id,
                scopes=tuple(scopes),
                kind=KIND_OAUTH,
                expires_at_ms=expires_at_ms,
                refresh_token_id=refresh_id,
                created_at_ms=now,
            ).to_entity(),
            {"operation_id": "TokenExchange"},
        )
        ctx.store.collection(COL.refresh_tokens).insert(
            RefreshTokenEntity(
                id=refresh_id,
                refresh_token=refresh_token,
                client_id=client_id,
                scopes=tuple(scopes),
                access_token_id=token_id,
                created_at_ms=now,
            ).to_entity(),
            {"operation_id": "TokenExchange"},
        )
        return TokenResponseWire(
            access_token=access_token,
            # "expires" is a Unix timestamp and "expires_in" the seconds
            # between now and it; both are documented response fields.
            expires=expires_at_ms // 1000,
            expires_in=config.access_token_ttl_s,
            refresh_token=refresh_token,
            domain_prefix=config.domain_prefix,
            scope=SCOPE_SEPARATOR.join(scopes),
        ).wire()


def auth_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedAuthSurface(deps).routes()


def _split_scopes(raw: str | None) -> tuple[str, ...]:
    """The authorize URL's ``scope`` parameter as a list.

    Split on whitespace **or** a literal ``+``: a consumer that hand-builds the
    URL often percent-decodes to ``a+b`` and one that uses a URL builder
    produces spaces. Both mean the same list.
    """
    if raw is None or not raw.strip():
        return ()
    return tuple(part for part in raw.replace("+", " ").split() if part)


def _with_query(url: str, params: Mapping[str, str | None]) -> str:
    """``url`` with ``params`` appended, dropping the ones with no value.

    ``state`` is echoed only when the authorization request sent one, which is
    what the documented URL's own optionality implies.
    """
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((name, value) for name, value in params.items() if value is not None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
