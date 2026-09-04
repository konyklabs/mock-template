"""The OAuth request and response shapes. JUDGMENT -- these coerce rather than validate strictly: the
OAuth bodies accept a form-encoded request, where every value arrives as a string, so strict
validation would reject ``short_lived=true`` for its encoding rather than its meaning.
https://developer.squareup.com/reference/square/oauth-api/obtain-token
DOCUMENTED -- ``redirect_uri`` is "Required if provided in the authorization URL"; declared here and
checked in the surface. https://developer.squareup.com/reference/square/oauth-api/obtain-token"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vendorfake.core.util.json import compact

__all__ = [
    "SQUARE_GRANT_TYPES",
    "SUPPORTED_GRANT_TYPES",
    "AuthorizationCodeGrant",
    "ObtainTokenEnvelope",
    "RefreshTokenGrant",
    "RevokeTokenRequest",
    "TokenResponse",
    "TokenStatusResponse",
]

SQUARE_GRANT_TYPES: tuple[str, ...] = ("authorization_code", "refresh_token", "migration_token")
"""DOCUMENTED grant types on ObtainToken. SHRINK -- only the first two are implemented; named here
so the unsupported-grant error can enumerate Square's real set.
https://developer.squareup.com/reference/square/oauth-api/obtain-token"""

SUPPORTED_GRANT_TYPES: tuple[str, ...] = ("authorization_code", "refresh_token")
"""What this unit actually honours; the error body lists these."""

#: Lax rather than strict (see the module docstring).
_REQUEST = ConfigDict(extra="ignore", frozen=True)

#: Responses are strict: a wrong type on the way out is a defect here.
_RESPONSE = ConfigDict(extra="forbid", frozen=True, strict=True)


def _list_or_none(value: Any) -> Any:
    """Keep a JSON array, read anything else as "not supplied" -- matters on the
    form-encoded path, where ``scopes=ORDERS_READ`` arrives as a bare string."""
    if value is None or isinstance(value, list | tuple):
        return value
    return None


class ObtainTokenEnvelope(BaseModel):
    """The two fields every ObtainToken request carries. Validated first: an
    unknown ``client_id`` is refused the same way whichever grant it asked for."""

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    grant_type: str = Field(min_length=1)


class AuthorizationCodeGrant(BaseModel):
    """``grant_type=authorization_code``. DOCUMENTED -- ``client_secret`` and ``code_verifier`` are
    optional here, required by the surface per the flow the code was issued under.
    https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope"""

    model_config = _REQUEST

    code: str = Field(min_length=1)
    client_secret: str | None = None
    code_verifier: str | None = None
    #: DOCUMENTED: "Required if provided in the authorization URL."
    #: https://developer.squareup.com/reference/square/oauth-api/obtain-token
    redirect_uri: str | None = None
    short_lived: bool = False
    scopes: list[str] | None = None

    _keep_arrays = field_validator("scopes", mode="before")(_list_or_none)


class RefreshTokenGrant(BaseModel):
    """``grant_type=refresh_token``: mint a new access token from a refresh token."""

    model_config = _REQUEST

    refresh_token: str = Field(min_length=1)
    client_secret: str | None = None
    #: Set on refresh, never cleared by it.
    short_lived: bool = False
    scopes: list[str] | None = None

    _keep_arrays = field_validator("scopes", mode="before")(_list_or_none)


class RevokeTokenRequest(BaseModel):
    """``POST /oauth2/revoke``. ``access_token`` and ``merchant_id`` are mutually exclusive and one
    is required; enforced in the surface, since each has its own error field.
    https://developer.squareup.com/reference/square/oauth-api/revoke-token"""

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    access_token: str | None = None
    merchant_id: str | None = None
    #: "terminates only the single token without ending the full authorization"
    revoke_only_access_token: bool = False


class TokenResponse(BaseModel):
    """The ObtainToken response, field for field and in Square's own order. DOCUMENTED --
    ``refresh_token_expires_at`` is emitted only for PKCE, since code-flow refresh tokens don't expire.
    https://developer.squareup.com/docs/oauth-api/overview"""

    model_config = _RESPONSE

    access_token: str
    token_type: str = "bearer"
    expires_at: str
    merchant_id: str
    refresh_token: str
    short_lived: bool
    refresh_token_expires_at: str | None = None

    def wire(self) -> dict[str, Any]:
        """``compact()`` keeps an absent optional absent rather than null."""
        return compact(
            {
                "access_token": self.access_token,
                "token_type": self.token_type,
                "expires_at": self.expires_at,
                "merchant_id": self.merchant_id,
                "refresh_token": self.refresh_token,
                "short_lived": self.short_lived,
                "refresh_token_expires_at": self.refresh_token_expires_at,
            }
        )


class TokenStatusResponse(BaseModel):
    """``POST /oauth2/token/status``.
    https://developer.squareup.com/reference/square/o-auth-api/retrieve-token-status"""

    model_config = _RESPONSE

    scopes: list[str]
    expires_at: str
    client_id: str
    merchant_id: str

    def wire(self) -> dict[str, Any]:
        return {
            "scopes": list(self.scopes),
            "expires_at": self.expires_at,
            "client_id": self.client_id,
            "merchant_id": self.merchant_id,
        }
