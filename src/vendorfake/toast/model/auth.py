"""The authentication vocabulary: the login request and its documented answer.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/authentication.html,
toast-authentication-api.yaml): the request
is ``{clientId, clientSecret, userAccessType}``; the answer is
``{"@class": ".SuccessfulResponse", "token": {tokenType, scope, expiresIn,
accessToken, idToken, refreshToken}, "status": "SUCCESS"}``, with the last
three emitted as the documented nulls -- a shape this project does not compact.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["MACHINE_CLIENT", "LoginRequest", "LoginResponseWire"]

MACHINE_CLIENT = "TOAST_MACHINE_CLIENT"
"""The one documented ``userAccessType``."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)
_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)


class LoginRequest(BaseModel):
    """Required strings are ``min_length=1`` so a form-encoded ``clientId=``
    and a missing key are the same ``missing_field`` (``model/common.py``)."""

    model_config = _REQUEST

    clientId: str = Field(min_length=1)
    clientSecret: str = Field(min_length=1)
    userAccessType: str = Field(min_length=1)


class LoginResponseWire(BaseModel):
    """The documented success document; key order is the page's."""

    model_config = _WIRE

    expiresIn: int
    accessToken: str

    def wire(self) -> dict[str, Any]:
        return {
            "@class": ".SuccessfulResponse",
            "token": {
                "tokenType": "Bearer",
                "scope": None,
                "expiresIn": self.expiresIn,
                "accessToken": self.accessToken,
                "idToken": None,
                "refreshToken": None,
            },
            "status": "SUCCESS",
        }
