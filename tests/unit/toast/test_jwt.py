"""The JWT-shaped token: three segments, decodable payload, HS256 signature."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from vendorfake.toast.jwt import decode_jwt_payload, mint_jwt, verify_jwt

SECRET = "unit-toast-jwt-signing-secret"
PAYLOAD = {"partner_guid": "0f6c1b1e-0000-4000-8000-00000000a0a0", "jti": "j1", "iat": 1, "exp": 19169}


def test_a_minted_token_has_three_base64url_segments_and_a_decodable_payload() -> None:
    token = mint_jwt(PAYLOAD, SECRET)
    parts = token.split(".")
    assert len(parts) == 3
    assert all(part and "=" not in part and "+" not in part and "/" not in part for part in parts)
    header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert decode_jwt_payload(token) == PAYLOAD


def test_the_signature_is_hs256_over_the_first_two_segments_written_out_independently() -> None:
    token = mint_jwt(PAYLOAD, SECRET)
    head, body, signature = token.split(".")
    expected = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    assert signature == expected.decode("ascii")
    assert verify_jwt(token, SECRET)
    assert not verify_jwt(token, "another-secret")
    assert not verify_jwt("not.a.jwt.at.all", SECRET)
    assert not verify_jwt("two.segments", SECRET)


def test_minting_is_deterministic_for_the_same_claims() -> None:
    assert mint_jwt(PAYLOAD, SECRET) == mint_jwt(dict(reversed(list(PAYLOAD.items()))), SECRET)
