"""A JWT-shaped access token, minted and checked with HS256.

FOR: producing the three-segment ``accessToken`` a Toast login answers with.
DOCUMENTED (https://doc.toasttab.com/doc/devguide/authentication.html): the
token is a JWT; https://doc.toasttab.com/doc/devguide/apiClientAccounts.html:
it "carries partner_guid or management_set_guid".

JUDGMENT -- everything else about it. The header is the conventional
``{"alg": "HS256", "typ": "JWT"}``; the payload carries ``partner_guid``
(documented), ``jti`` (the token record's id, so a token maps to one stored
record), ``iat`` and ``exp`` (Unix seconds, so a JWT-aware client sees the
same lifetime ``expiresIn`` states), and ``scope`` (the space-joined scope set,
for a consumer decoding it in a debugger). The signing secret is this unit's
(``ToastConfig.jwt_signing_secret``); Toast's real key is not something a
consumer ever holds, and a consumer must never verify a Toast token locally.

The auth adapter does NOT verify the signature to authenticate: it looks the
presented string up in the token store, exactly as an opaque token would be
looked up, so a token minted by another unit -- or with the signing secret --
is refused as unknown. :func:`verify_jwt` exists for tests and for a consumer
who wants to see that the segments are what they claim.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from vendorfake.core.util.b64 import b64url_decode, b64url_encode

__all__ = ["decode_jwt_payload", "mint_jwt", "verify_jwt"]

_HEADER = b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))


def _signature(signing_input: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return b64url_encode(digest)


def mint_jwt(payload: Mapping[str, Any], secret: str) -> str:
    """``header.payload.signature``, base64url without padding, HS256."""
    body = b64url_encode(json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{_HEADER}.{body}"
    return f"{signing_input}.{_signature(signing_input, secret)}"


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """The payload segment, decoded and parsed. No verification."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("a JWT has exactly three segments")
    decoded = json.loads(b64url_decode(parts[1]).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("the JWT payload is not an object")
    return decoded


def verify_jwt(token: str, secret: str) -> bool:
    """Whether the signature segment is HS256 over the first two under ``secret``."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    return hmac.compare_digest(_signature(f"{parts[0]}.{parts[1]}", secret), parts[2])
