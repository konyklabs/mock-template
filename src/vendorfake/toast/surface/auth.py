"""The authentication surface: one endpoint, the login.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/authentication.html,
toast-authentication-api.yaml): the body is ``{clientId, clientSecret,
userAccessType: "TOAST_MACHINE_CLIENT"}``; the answer is
``{"@class": ".SuccessfulResponse", "token": {...}, "status": "SUCCESS"}``
with ``expiresIn`` counting down the token's remaining lifetime; 401 for bad
credentials; no refresh flow.

JUDGMENT: the token lifetime defaults to the documented example (19168 s);
an unrecognized ``userAccessType`` is a 400 naming the field; a minted token
is journalled as a ``Login`` operation, since it is observable state.

INVARIANT: no 4xx leaves a journal entry or draws an id.
"""

from __future__ import annotations

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.toast.entities import COL, TokenEntity
from vendorfake.toast.jwt import mint_jwt
from vendorfake.toast.model.auth import MACHINE_CLIENT, LoginRequest, LoginResponseWire
from vendorfake.toast.model.common import validate_body
from vendorfake.toast.paths import LOGIN
from vendorfake.toast.surface.common import ToastDeps, now_ms

__all__ = ["CAPABILITY", "INVALID_CREDENTIALS_MESSAGE", "LOGIN_PATH", "ToastAuthSurface", "auth_routes"]

CAPABILITY = "auth"

LOGIN_PATH = LOGIN
"""Deprecated alias of ``vendorfake.toast.paths.LOGIN``, kept for v0.1.0
consumers; new code should import ``LOGIN`` directly."""

INVALID_CREDENTIALS_MESSAGE = "The credentials in your request are not valid."
"""The documented 401 phrase, as this unit prints it."""


class ToastAuthSurface:
    """The login route, bound to one vendor's config and id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `example_body`: C18's committed-mutation contract targets the
        # first example route, and login produces no webhook-visible mutation.
        return (
            Route(
                method="POST",
                path=LOGIN_PATH,
                capability=CAPABILITY,
                handler=self.login,
                operation_id="Login",
                summary="Exchange clientId/clientSecret for a Bearer JWT; 401 on bad credentials; no refresh.",
            ),
        )

    def login(self, args: HandlerArgs) -> ReplyInit:
        config = self._deps.config
        request = validate_body(LoginRequest, args.body())
        if request.userAccessType != MACHINE_CLIENT:
            # JUDGMENT: the spec documents one value and nothing about another.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"userAccessType must be {MACHINE_CLIENT}.",
                field="userAccessType",
                info={"supplied": request.userAccessType},
            )
        if request.clientId != config.client_id or request.clientSecret != config.client_secret:
            # One phrase for both mismatches, since naming which half was
            # wrong would leak information the real endpoint does not.
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail=INVALID_CREDENTIALS_MESSAGE,
                info={"reason": "client_id_mismatch" if request.clientId != config.client_id else "secret_mismatch"},
            )
        # Every refusal is above this line; the mint and its journal entry
        # are below.
        now = now_ms(args.ctx)
        expires_at_ms = now + config.access_token_ttl_ms
        token_id = self._deps.ids.token_id()
        access_token = mint_jwt(
            {
                "partner_guid": config.partner_guid,
                "jti": token_id,
                "iat": now // 1000,
                "exp": expires_at_ms // 1000,
                "scope": " ".join(config.scopes),
            },
            config.jwt_signing_secret,
        )
        args.ctx.store.collection(COL.tokens).insert(
            TokenEntity(
                id=token_id,
                access_token=access_token,
                client_id=config.client_id,
                partner_guid=config.partner_guid,
                expires_at_ms=expires_at_ms,
                scopes=config.scopes,
                createdDate=now,
            ).to_entity(),
            {"operation_id": "Login"},
        )
        return json_(LoginResponseWire(expiresIn=config.access_token_ttl_s, accessToken=access_token).wire())


def auth_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastAuthSurface(deps).routes()
