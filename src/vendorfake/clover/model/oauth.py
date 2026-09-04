"""The OAuth v2 vocabulary: the app, an authorization code, and the token
response for ``POST /oauth/v2/token`` and ``POST /oauth/v2/refresh``.

DOCUMENTED: expirations are Unix seconds, unlike every entity timestamp here
(https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token).
JUDGMENT: the app and code models are internal vocabulary -- Clover documents
the flow (https://docs.clover.com/dev/docs/high-trust-app-auth-flow) but no
entity shapes for either.
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
"""Lax parse path; empty strings are ``missing_field`` (``model/common.py``)."""

_RESPONSE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Projection-only: strict, since this unit emits these itself."""


class AppModel(BaseModel):
    """The installed app: credential pair and permission set every minted token inherits."""

    model_config = _RESPONSE

    client_id: str
    client_secret: str
    permissions: tuple[str, ...]


class AuthorizationCodeModel(BaseModel):
    """One authorization code; ``created_at_ms`` backs the JUDGMENT ten-minute expiry (``config.py``)."""

    model_config = _RESPONSE

    code: str
    merchant_id: str
    client_id: str
    created_at_ms: int


class TokenRequest(BaseModel):
    """``POST /oauth/v2/token``: high-trust sends ``{client_id,
    client_secret, code}``, PKCE sends ``{client_id, code, code_verifier}``."""

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    client_secret: str | None = None
    code_verifier: str | None = None


class RefreshRequest(BaseModel):
    """``POST /oauth/v2/refresh`` -- ``{client_id, refresh_token}``, no
    ``client_secret`` (https://docs.clover.com/dev/docs/refresh-access-tokens)."""

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """The documented four-field token response; expirations in Unix seconds, no field optional."""

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
