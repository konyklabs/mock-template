"""Auth surface: the token endpoint, and a stand-in for the authorize redirect.

Neither route is in ``api-2026-07.yaml``; both are documented only in prose at
https://x-series-api.lightspeedhq.com/docs/authorization.

DOCUMENTED: authorize is ``GET /connect?response_type=code&client_id=...&redirect_uri=...
&state=...&scope=...``; exchange takes ``code``/``client_id``/``client_secret``/
``grant_type=authorization_code`` plus ``redirect_uri``; refresh takes
``grant_type=refresh_token``. Response carries ``access_token``, ``token_type``,
``expires``, ``expires_in``, ``refresh_token``, ``domain_prefix``, ``scope``. Refreshing
retires the consumed refresh token AND revokes the access token issued with it.

JUDGMENT: ``GET /connect`` stands in for the real consent screen on
``secure.retail.lightspeed.app`` -- it approves automatically and redirects with the
code (labelled "Stand-in" in ``GET /__unit/routes``). The code's ten-minute, single-use
lifetime is from roadmap#75. A spent or reused credential is a 401. ``client_secret`` is
required on refresh too (``model/auth.py``). JSON is accepted alongside the documented
form encoding, since body parsing here is content-type general.
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
        # No `example_body`: a token mint isn't a mutation the webhook mapper
        # announces; `surface/sales.py`'s create is the example route instead.
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
            # JUDGMENT: any other response_type is a 400 rather than silently
            # treated as `code`.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"response_type must be {RESPONSE_TYPE!r}; the authorization flow documents no other value.",
                field="response_type",
                info={"supplied": response_type},
            )
        # The effective redirect_uri (supplied, or the unit's configured
        # default) is what gets recorded on the code, and the exchange must
        # match it exactly: the code is bound to client_id + scope + redirect_uri.
        redirect_uri = args.query("redirect_uri") or config.redirect_uri
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
                redirect_uri=redirect_uri,
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
        if grant.redirect_uri != record.redirect_uri:
            # Checked unconditionally: the code always carries the effective
            # URL it was issued for (see `connect`), so there's no way to skip
            # the comparison.
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
        # Rotation: the consumed refresh token is retired, and the access
        # token issued with it is revoked.
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
            # Same message for a wrong id or wrong secret: naming which one
            # leaks info the real endpoint doesn't.
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
            # `expires`: unix timestamp; `expires_in`: seconds from now -- both documented.
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

    Splits on whitespace or a literal ``+`` -- a hand-built URL often
    percent-decodes to ``a+b``; a URL builder produces spaces.
    """
    if raw is None or not raw.strip():
        return ()
    return tuple(part for part in raw.replace("+", " ").split() if part)


def _with_query(url: str, params: Mapping[str, str | None]) -> str:
    """``url`` with ``params`` appended, dropping the ones with no value (``state`` is optional)."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((name, value) for name, value in params.items() if value is not None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
