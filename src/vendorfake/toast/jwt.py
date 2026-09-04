"""A JWT-shaped access token, minted and checked with HS256.

DOCUMENTED: the token is a JWT carrying ``partner_guid``
(https://doc.toasttab.com/doc/devguide/authentication.html,
https://doc.toasttab.com/doc/devguide/apiClientAccounts.html).

JUDGMENT: everything else is this project's shape; a consumer must never
verify a Toast token locally. The auth adapter authenticates by token-store
lookup, not signature verification; :func:`verify_jwt` is for tests only.
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
