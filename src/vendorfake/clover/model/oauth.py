"""The OAuth v2 vocabulary: the app, an authorization code, and the token response.

FOR: stating once what ``POST /oauth/v2/token`` and ``POST /oauth/v2/refresh``
return and what the flow's intermediate artifacts carry, so the PR-B surface
reads fields off typed objects instead of indexing ``dict[str, Any]``.

The token response is DOCUMENTED, verbatim, on
https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token:

    {"access_token": "...", "access_token_expiration": 1677875430,
     "refresh_token": "...", "refresh_token_expiration": 1709497830}

Both expirations are **Unix seconds** -- unlike every entity timestamp in this
package, which is Unix **milliseconds** (``modifiedTime: 1755786102000`` in
the inventory create example). The two units coexisting on one API is exactly
the kind of thing a consumer's client gets wrong, so this fake preserves it
and a test pins it.

The refresh endpoint takes ``{client_id, refresh_token}`` -- **no
client_secret** (https://docs.clover.com/dev/docs/refresh-access-tokens), and
"Refresh token is for single use and becomes invalid immediately after a new
access_token and refresh_token pair is generated" (same page, verbatim; the
rotation behaviour lands with the PR-B surface).

JUDGMENT -- the app and authorization-code models are this unit's internal
vocabulary: Clover documents the *flow* (authorize redirect carrying
``merchant_id``, ``client_id`` and ``code``,
https://docs.clover.com/dev/docs/high-trust-app-auth-flow) but no entity
shapes for either, and no OAuth error bodies at all.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AppModel",
    "AuthorizationCodeModel",
    "RefreshRequest",
    "TokenRequest",
    "TokenResponse",
]

_REQUEST = ConfigDict(extra="ignore", frozen=True)
"""The parse path, per the package convention on ``model/order.py``: lax and
tolerant of extra keys. Required strings are ``min_length=1`` so that an empty
form value and a missing key are the same ``missing_field``; see
``model/common.py``."""

_RESPONSE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Projection-only: this unit *emits* the token response and builds the app
and code records itself, so these stay strict -- an expiration that arrived as
a float or a string is this unit's own bug, refused here rather than coerced
on the way to the wire. The PR-B *request* models (token exchange, refresh)
will use the lax ``_REQUEST`` convention from ``model/order.py``."""


class AppModel(BaseModel):
    """The installed app: the credential pair and the permission set every
    minted token inherits. JUDGMENT -- internal vocabulary, not a Clover wire
    shape; see the module docstring and ``config.py`` on permissions."""

    model_config = _RESPONSE

    client_id: str
    client_secret: str
    permissions: tuple[str, ...]


class AuthorizationCodeModel(BaseModel):
    """One authorization code, as the redirect mints it.

    The redirect query is documented -- ``?merchant_id=...&client_id=...&code=...``
    (https://docs.clover.com/dev/docs/high-trust-app-auth-flow) -- which is
    where these three fields come from. ``created_at_ms`` is internal
    bookkeeping for the JUDGMENT ten-minute expiry in ``config.py``.
    """

    model_config = _RESPONSE

    code: str
    merchant_id: str
    client_id: str
    created_at_ms: int


class TokenRequest(BaseModel):
    """``POST /oauth/v2/token`` -- both documented variants in one model.

    High-trust sends ``{client_id, client_secret, code}``; PKCE sends
    ``{client_id, code, code_verifier}``
    (https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token).
    The two optionals are optional *here* because which one is required
    depends on how the code was issued -- a code minted with a
    ``code_challenge`` demands the verifier, any other demands the secret --
    and that routing lives in the surface, where the code record is.
    """

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    client_secret: str | None = None
    code_verifier: str | None = None


class RefreshRequest(BaseModel):
    """``POST /oauth/v2/refresh`` -- ``{client_id, refresh_token}``, and
    deliberately NO ``client_secret``: the documented refresh body does not
    carry one (https://docs.clover.com/dev/docs/refresh-access-tokens). One
    sent anyway is ignored by ``extra="ignore"`` rather than refused, which is
    where the documentation stops too.
    """

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """The documented four-field token response. Expirations in Unix SECONDS.

    Emitted verbatim by both ``/oauth/v2/token`` and ``/oauth/v2/refresh``
    (https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token,
    https://docs.clover.com/dev/docs/refresh-access-tokens). No field is
    optional; all four appear in every documented example.
    """

    model_config = _RESPONSE

    access_token: str
    access_token_expiration: int
    refresh_token: str
    refresh_token_expiration: int

    def wire(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "access_token_expiration": self.access_token_expiration,
            "refresh_token": self.refresh_token,
            "refresh_token_expiration": self.refresh_token_expiration,
        }
