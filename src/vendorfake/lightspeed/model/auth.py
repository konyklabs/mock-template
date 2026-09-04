"""The authentication vocabulary: the token request and its documented answer.

DOCUMENTED, verbatim, on https://x-series-api.lightspeedhq.com/docs/authorization
(the token endpoint is NOT in ``api-2026-07.yaml`` at all -- it lives under
``api/1.0`` and is documented only in prose, which is why every field here is
cited to that page rather than to a schema).

The authorize URL::

    https://secure.retail.lightspeed.app/connect?response_type=code
        &client_id={client_id}&redirect_uri={redirect_uri}
        &state={state}&scope={scope}

The token request (form-encoded), for the initial exchange: ``code``,
``client_id``, ``client_secret``, ``grant_type`` (always
``authorization_code`` here), ``redirect_uri``. The refresh call sends
``grant_type=refresh_token`` with the stored ``refresh_token``.

The response fields, exactly these seven: ``access_token``, ``token_type``
("Bearer"), ``expires`` (a Unix timestamp), ``expires_in`` (seconds),
``refresh_token``, ``domain_prefix``, ``scope``.

JUDGMENT, each labelled at its site:

* ``expires_in`` is 86400 in the page's own EXAMPLE and is stated nowhere as a
  rule (``config.py``);
* the refresh token's lifetime is NOT FOUND anywhere, so a refresh token here
  never expires and is only ever retired by use (``capabilities.py``);
* the ``scope`` member's separator. The authorize URL's ``scope`` parameter is
  a list and the page shows no delimiter for either direction; this package
  uses a single space, which is what RFC 6749 specifies for an OAuth 2.0
  ``scope`` value and therefore the reading a consumer's OAuth library will
  already take.
"""

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
"""The two grants the authorization page documents. There is no client-credentials
flow and no implicit flow."""

TOKEN_TYPE = "Bearer"
"""The documented ``token_type``."""

SCOPE_SEPARATOR = " "
"""JUDGMENT -- see the module docstring."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)
_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)


class TokenEnvelope(BaseModel):
    """The two fields every token request carries, whichever grant it is.

    Required strings are ``min_length=1`` so a form-encoded ``client_id=`` and
    a missing key are the same ``missing_field`` (``model/common.py``).
    """

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
    """``grant_type=refresh_token``.

    ``client_secret`` is required here as well: the page documents the secret
    on the initial exchange and does not repeat the parameter list for the
    refresh, and an unauthenticated refresh would let anyone holding a leaked
    refresh token mint an access token. JUDGMENT, taken in the direction that
    cannot teach a consumer a weaker rule than the real API's.
    """

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
