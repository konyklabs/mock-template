"""The authentication vocabulary: the token request and its documented answer.
DOCUMENTED, verbatim, on
https://x-series-api.lightspeedhq.com/docs/authorization -- the token
endpoint is not in ``api-2026-07.yaml`` at all, so every field here is cited
to that prose page rather than a schema. ``expires`` is a Unix timestamp;
``expires_in`` is seconds.
JUDGMENT, each labelled at its site: ``expires_in`` (``config.py``) -- 86400
in the page's own EXAMPLE, stated nowhere as a rule; the refresh token's
lifetime (``capabilities.py``) -- not found anywhere, so it never expires;
the ``scope`` separator -- undocumented, so this package uses RFC 6749's
single space."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "GRANT_AUTHORIZATION_CODE",
    "GRANT_REFRESH_TOKEN",
    "SCOPE_SEPARATOR",
    "SUPPORTED_GRANT_TYPES",
    "TOKEN_TYPE",
    "AuthorizationCodeGrant",
    "RefreshTokenGrant",
    "TokenEnvelope",
    "TokenResponseWire",
]

GRANT_AUTHORIZATION_CODE = "authorization_code"
GRANT_REFRESH_TOKEN = "refresh_token"
SUPPORTED_GRANT_TYPES: tuple[str, ...] = (GRANT_AUTHORIZATION_CODE, GRANT_REFRESH_TOKEN)
"""The two grants the authorization page documents."""

TOKEN_TYPE = "Bearer"
"""The documented ``token_type``."""

SCOPE_SEPARATOR = " "
"""JUDGMENT -- see the module docstring."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)
_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)


class TokenEnvelope(BaseModel):
    """The two fields every token request carries. ``min_length=1`` so a
    form-encoded ``client_id=`` and a missing key are the same
    ``missing_field`` (``model/common.py``)."""

    model_config = _REQUEST

    grant_type: str = Field(min_length=1)
    client_id: str = Field(min_length=1)


class AuthorizationCodeGrant(BaseModel):
    """``grant_type=authorization_code``: the five documented parameters."""

    model_config = _REQUEST

    code: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    redirect_uri: str | None = None


class RefreshTokenGrant(BaseModel):
    """``grant_type=refresh_token``. ``client_secret`` is required here too
    (JUDGMENT): an unauthenticated refresh would let a leaked token mint an
    access token."""

    model_config = _REQUEST

    refresh_token: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class TokenResponseWire(BaseModel):
    """The documented success document; key order is the page's."""

    model_config = _WIRE

    access_token: str
    expires: int
    expires_in: int
    refresh_token: str
    domain_prefix: str
    scope: str

    def wire(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": TOKEN_TYPE,
            "expires": self.expires,
            "expires_in": self.expires_in,
            "refresh_token": self.refresh_token,
            "domain_prefix": self.domain_prefix,
            "scope": self.scope,
        }
