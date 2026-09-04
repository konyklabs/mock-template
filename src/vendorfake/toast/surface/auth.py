"""The authentication surface: one endpoint, the login.

FOR: reproducing ``POST /authentication/v1/authentication/login`` with the
documented request, the documented success document and the documented 401 --
so that an integration's token handling passes here exactly as it would
against Toast.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/authentication.html,
toast-authentication-api.yaml):

* the body is ``{clientId, clientSecret, userAccessType: "TOAST_MACHINE_CLIENT"}``;
* the answer is ``{"@class": ".SuccessfulResponse", "token": {...}, "status":
  "SUCCESS"}`` with ``expiresIn`` "the number of seconds that the
  authentication token is valid";
* 401: "the credentials in your request are not valid";
* there is no refresh: a client logs in again when the token expires.

JUDGMENT, each labelled at its site:

* **the token lifetime** is the configured ``access_token_ttl_s``, defaulting
  to the one documented example (19168 s);
* **``userAccessType``** other than ``TOAST_MACHINE_CLIENT`` is a 400 naming
  the field -- the specification documents one value and no answer to
  another;
* **the token's claims** beyond ``partner_guid`` (``jwt.py``);
* **the token is journalled** as a ``Login`` operation: a minted token is real
  state a consumer can observe (it authenticates), and the seed loads its
  tokens the same way.

THE ORDERING INVARIANT: **no 4xx leaves a journal entry or draws an id.** The
credential check precedes the mint; a refused login leaves the world exactly
as it found it.
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
"""Deprecated alias of ``vendorfake.toast.paths.LOGIN``, kept because v0.1.0
consumers may already import it from here. New code should import ``LOGIN``
from ``vendorfake.toast.paths``, which ``tests/unit/test_paths_drift.py``
keeps honest against the route table; this name will not be removed without a
CHANGELOG entry of its own."""

INVALID_CREDENTIALS_MESSAGE = "The credentials in your request are not valid."
"""The documented 401 phrase, as this unit prints it."""


class ToastAuthSurface:
    """The login route, bound to one vendor's config and id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `example_body`, deliberately: the conformance suite aims its
        # committed-mutation contracts at the FIRST example route, and a login
        # produces a token the webhook mapper (rightly) never announces, so
        # C18 would measure nothing. POST /orders is that route.
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
            # Documented 401. One phrase for a wrong id and a wrong secret: the
            # page says "credentials", and naming which half was wrong would
            # be telling an attacker something the real endpoint does not.
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
