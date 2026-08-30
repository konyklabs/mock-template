"""The authentication vocabulary: the login request and its documented answer.

DOCUMENTED, verbatim, on https://doc.toasttab.com/doc/devguide/authentication.html
(and toast-authentication-api.yaml)::

    POST /authentication/v1/authentication/login
    {"clientId": "...", "clientSecret": "...", "userAccessType": "TOAST_MACHINE_CLIENT"}

    {"@class": ".SuccessfulResponse",
     "token": {"tokenType": "Bearer", "scope": null, "expiresIn": 19168,
               "accessToken": "<JWT>", "idToken": null, "refreshToken": null},
     "status": "SUCCESS"}

``idToken`` and ``refreshToken`` are "for internal use only" and ``scope`` is
null in the example; all three are emitted as the documented nulls, so this is
a second shape (after the ErrorMessage) that is not compacted.
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
