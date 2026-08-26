"""The OAuth request and response shapes, as models rather than as key lookups.

FOR: stating once what ``POST /oauth2/token`` and ``POST /oauth2/revoke``
accept and what ObtainToken returns, so that the surface reads fields off a
typed object instead of indexing a ``dict[str, Any]`` and re-deriving "is this
a non-empty string" at every use.

INVARIANT: **these models coerce, and that is a deliberate exception.** Every
other request model in this build is ``strict=True``, because the reference
distinguishes ``{"version": "3"}`` (rejected) from ``{"version": 3.5}``
(accepted, then refused for a different reason) and lax coercion collapses the
two. The OAuth bodies are the one place coercion is chosen on purpose, for one
reason: this unit accepts a form-encoded body here, and in a form-encoded body
*every* value is a string. Under strict validation ``short_lived=true`` over
``application/x-www-form-urlencoded`` could only ever be a type error.

JUDGMENT, and it is a real behaviour difference from the reference. The
reference tests ``body.short_lived === true``, so on its own form-encoded path
the string ``"true"`` is **false** and a consumer asking for a 24-hour token
silently receives a 30-day one. That is not a Square behaviour -- Square
documents this endpoint as ``application/json`` only
(https://developer.squareup.com/reference/square/oauth-api/obtain-token) and
publishes nothing about form encoding at all -- so there is no documented
answer to defer to, and the choice is between two mock conventions. Coercion is
the one that fails on the thing under test rather than on the encoding, so
``short_lived=true``, ``short_lived=1`` and ``"short_lived": true`` all mean the
same thing here. The cost, stated plainly: a JSON body carrying
``"short_lived": "true"`` is also accepted here where the reference refused it.

The second departure from the reference is ``redirect_uri``. Square documents
it on ObtainToken as "Required if provided in the authorization URL"; the
reference stores it on the authorization-code entity and never looks at it
again, so a consumer testing the mismatch case gets a false pass. It is
declared here and checked in the surface.
"""

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
"""The three grant types Square documents on ObtainToken.

https://developer.squareup.com/reference/square/oauth-api/obtain-token

SHRINK -- this unit implements the first two. ``migration_token`` exists to move
a pre-OAuth personal access token onto the OAuth model, which is a one-way
migration of a credential this fake never issued; there is nothing for it to
migrate. It is named here rather than forgotten so that the unsupported-grant
error can enumerate Square's real set and a reader can see the omission is a
decision. The reference dropped it silently.
"""

SUPPORTED_GRANT_TYPES: tuple[str, ...] = ("authorization_code", "refresh_token")
"""What this unit actually honours; the error body lists these."""

#: Lax rather than strict, and extras ignored rather than forbidden. See the
#: module docstring for the coercion decision. Extras are ignored because a
#: consumer's OAuth client library sends parameters this unit does not model
#: (``state``, ``session``, ``migration_token``) and refusing the request over
#: one of them would fail on the encoding rather than on the thing under test.
_REQUEST = ConfigDict(extra="ignore", frozen=True)

#: Responses are strict: a value on its way out of this unit is produced here,
#: so a wrong type is a defect in this package and coercing it would hide one.
_RESPONSE = ConfigDict(extra="forbid", frozen=True, strict=True)


def _list_or_none(value: Any) -> Any:
    """Keep a JSON array, and read anything else as "not supplied".

    The reference's ``Array.isArray(body.scopes) ? body.scopes : fallback``,
    preserved rather than tightened. It matters most on the form-encoded path,
    where ``scopes=ORDERS_READ`` arrives as a bare string: the reference falls
    back to the scopes already on the record, and refusing the request instead
    would make the mock's own affordance the thing that broke.
    """
    if value is None or isinstance(value, list | tuple):
        return value
    return None


class ObtainTokenEnvelope(BaseModel):
    """The two fields every ObtainToken request carries, whatever the grant.

    Validated on its own and first, because the reference checks ``client_id``
    against the configured application *before* it looks at the grant -- so an
    unknown application is refused with the same error whichever grant it
    asked for.
    """

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    grant_type: str = Field(min_length=1)


class AuthorizationCodeGrant(BaseModel):
    """``grant_type=authorization_code``: exchange a code for a token.

    ``client_secret`` and ``code_verifier`` are both optional *here* and
    required by the surface according to the flow the code was issued under:
    "client_secret required on code-flow refresh, absent on PKCE"
    (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope),
    and a code that carried a ``code_challenge`` is a PKCE code. Making them
    conditionally required in the model would put the flow rule in two places.
    """

    model_config = _REQUEST

    code: str = Field(min_length=1)
    client_secret: str | None = None
    code_verifier: str | None = None
    #: "Required if provided in the authorization URL."
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
    #: Set on refresh, never cleared by it. The reference merges
    #: ``body.short_lived === true ? true : existing.shortLived``; typing this
    #: ``bool | None`` and testing ``is not None`` would silently add a path
    #: that turns a short-lived token back into a 30-day one.
    short_lived: bool = False
    scopes: list[str] | None = None

    _keep_arrays = field_validator("scopes", mode="before")(_list_or_none)


class RevokeTokenRequest(BaseModel):
    """``POST /oauth2/revoke``.

    ``access_token`` and ``merchant_id`` are mutually exclusive and one is
    required; both rules are enforced in the surface rather than here, because
    each has its own documented error field and a model-level check reports the
    whole model rather than the field a consumer must fix.
    https://developer.squareup.com/reference/square/oauth-api/revoke-token
    """

    model_config = _REQUEST

    client_id: str = Field(min_length=1)
    access_token: str | None = None
    merchant_id: str | None = None
    #: "terminates only the single token without ending the full authorization"
    revoke_only_access_token: bool = False


class TokenResponse(BaseModel):
    """The ObtainToken response, field for field and in Square's own order.

    https://developer.squareup.com/reference/square/oauth-api/obtain-token

    ``refresh_token_expires_at`` is emitted only for the PKCE flow: "Refresh
    tokens obtained using the PKCE flow are single-use tokens and expire after
    90 days", while "Refresh tokens obtained using the code flow don't expire"
    (https://developer.squareup.com/docs/oauth-api/overview), so on the code
    flow the key is absent rather than null.

    The legacy ``subscription_id``, ``plan_id`` and ``id_token`` fields are not
    modelled; ``id_token`` belongs to OpenID Connect and the other two to a
    subscription product this unit does not imitate.
    """

    model_config = _RESPONSE

    access_token: str
    token_type: str = "bearer"
    expires_at: str
    merchant_id: str
    refresh_token: str
    short_lived: bool
    refresh_token_expires_at: str | None = None

    def wire(self) -> dict[str, Any]:
        """The response body. ``compact()`` is what keeps an absent optional
        absent rather than null; see :mod:`vendorfake.square.entities`."""
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

    https://developer.squareup.com/reference/square/o-auth-api/retrieve-token-status
    """

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
