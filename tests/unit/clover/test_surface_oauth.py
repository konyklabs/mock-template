"""The OAuth v2 surface: documented redirect, four-field response, rotation.

The two invariants under the heaviest test here are the ones the Square
adversarial rounds proved fakes lie about: refresh rotation (single-use,
documented verbatim) and the no-journal-entry-on-4xx ordering rule.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.clover.harness import (
    CLIENT_ID,
    CLIENT_SECRET,
    CONFIGURED_REDIRECT_URI,
    MERCHANT_ID,
    Harness,
    harness,
)
from vendorfake.clover.entities import COL, AuthorizationCodeEntity, TokenEntity
from vendorfake.core.util.b64 import b64url_encode

DAY_S = 24 * 60 * 60


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def _location_query(response) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
    return parse_qs(urlsplit(response.headers["location"]).query)


# ---------------------------------------------------------------------------
# GET /oauth/v2/authorize
# ---------------------------------------------------------------------------


def test_the_redirect_carries_merchant_id_client_id_and_code(h: Harness) -> None:
    """The documented callback shape -- Clover, unlike Square, identifies the
    merchant and echoes the app: ?merchant_id=...&client_id=...&code=...
    (https://docs.clover.com/dev/docs/high-trust-app-auth-flow)."""
    response = h.authorize()
    assert response.status == 302
    location = response.headers["location"]
    assert location.startswith(CONFIGURED_REDIRECT_URI)
    query = _location_query(response)
    assert query["merchant_id"] == [MERCHANT_ID]
    assert query["client_id"] == [CLIENT_ID]
    assert len(query["code"]) == 1
    assert "state" not in query  # none sent, none invented


def test_state_is_echoed_when_sent(h: Harness) -> None:
    """JUDGMENT: not in Clover's documented redirect; echoed because every
    standard client library sends and verifies it."""
    query = _location_query(h.authorize(state="xyzzy&with=amp"))
    assert query["state"] == ["xyzzy&with=amp"]


def test_an_unknown_client_id_is_refused(h: Harness) -> None:
    response = h.api.call(method="GET", path="/oauth/v2/authorize", query={"client_id": "WRONGAPP12345"})
    assert response.status == 400
    assert "client_id" in response.json()["message"]


def test_a_mismatched_redirect_uri_is_refused(h: Harness) -> None:
    """Redirecting a code to an unregistered URI is the attack the parameter
    exists to prevent; the fake must not teach that it works."""
    response = h.authorize(redirect_uri="https://evil.test/steal")
    assert response.status == 400
    assert "redirect_uri" in response.json()["message"]


def test_a_matching_redirect_uri_is_accepted(h: Harness) -> None:
    assert h.authorize(redirect_uri=CONFIGURED_REDIRECT_URI).status == 302


# ---------------------------------------------------------------------------
# POST /oauth/v2/token -- high-trust
# ---------------------------------------------------------------------------


def test_the_documented_four_field_response_in_unix_seconds(h: Harness) -> None:
    """Exactly {access_token, access_token_expiration, refresh_token,
    refresh_token_expiration}; expirations Unix SECONDS -- 30 minutes
    documented, 365 days JUDGMENT (labels in config.py)."""
    before = int(time.time())
    body = h.exchange()
    assert set(body) == {
        "access_token",
        "access_token_expiration",
        "refresh_token",
        "refresh_token_expiration",
    }
    # Seconds, not milliseconds: a ms value here would be ~1000x larger.
    assert abs(body["access_token_expiration"] - (before + 1800)) <= 5
    assert abs(body["refresh_token_expiration"] - (before + 365 * DAY_S)) <= 5


def test_a_code_is_single_use(h: Harness) -> None:
    code = h.code()
    assert h.token(client_secret=CLIENT_SECRET, code=code).status == 200
    replay = h.token(client_secret=CLIENT_SECRET, code=code)
    assert replay.status == 401
    assert replay.json()["message"] == "Failed to validate authentication code"
    assert replay.json()["unit_error"]["reason"] == "already_used"


def test_an_unknown_code_gets_the_documented_faq_phrase(h: Harness) -> None:
    response = h.token(client_secret=CLIENT_SECRET, code="not-a-code")
    assert response.status == 401
    assert response.json()["message"] == "Failed to validate authentication code"


def test_an_expired_code_is_refused_with_the_same_phrase(h: Harness) -> None:
    """The wire does not say which way the code was bad; the sidecar does."""
    code = h.code()
    codes = h.unit.context.store.collection(COL.codes)

    def expire(draft: dict) -> None:  # type: ignore[type-arg]
        draft["expires_at_ms"] = 1

    codes.update(code, expire, silent=True)
    response = h.token(client_secret=CLIENT_SECRET, code=code)
    assert response.status == 401
    assert response.json()["message"] == "Failed to validate authentication code"
    assert response.json()["unit_error"]["reason"] == "expired"


def test_a_wrong_client_secret_is_401_and_a_missing_one_names_the_field(h: Harness) -> None:
    assert h.token(client_secret="wrong", code=h.code()).status == 401
    missing = h.token(code=h.code())
    assert missing.status == 400
    assert missing.json()["unit_error"]["field"] == "client_secret"


def test_a_wrong_client_id_at_exchange_is_401(h: Harness) -> None:
    response = h.api.post(
        "/oauth/v2/token",
        {"client_id": "WRONGAPP12345", "client_secret": CLIENT_SECRET, "code": h.code()},
    )
    assert response.status == 401


# ---------------------------------------------------------------------------
# POST /oauth/v2/token -- PKCE
# ---------------------------------------------------------------------------

VERIFIER = "correct-horse-battery-staple-0123456789abcdef"
CHALLENGE = b64url_encode(hashlib.sha256(VERIFIER.encode()).digest())


def test_a_pkce_exchange_proves_the_verifier_and_needs_no_secret(h: Harness) -> None:
    code = h.code(code_challenge=CHALLENGE, code_challenge_method="S256")
    response = h.token(code=code, code_verifier=VERIFIER)
    assert response.status == 200
    assert set(response.json()) == {
        "access_token",
        "access_token_expiration",
        "refresh_token",
        "refresh_token_expiration",
    }


def test_a_wrong_verifier_is_401_and_a_missing_one_names_the_field(h: Harness) -> None:
    code = h.code(code_challenge=CHALLENGE)
    assert h.token(code=code, code_verifier="not-the-verifier").status == 401
    missing = h.token(code=code)
    assert missing.status == 400
    assert missing.json()["unit_error"]["field"] == "code_verifier"


def test_only_s256_is_accepted_at_authorize(h: Harness) -> None:
    response = h.authorize(code_challenge=CHALLENGE, code_challenge_method="plain")
    assert response.status == 400


# ---------------------------------------------------------------------------
# The ordering invariant: no 4xx leaves a journal entry, and a refused
# request never burns the credential it refused.
# ---------------------------------------------------------------------------


def test_a_refused_exchange_journals_nothing_and_the_code_survives(h: Harness) -> None:
    """The N-1 class from the Square build: `_check_secret` runs before the
    mark-used write, so a consumer who fat-fingers the secret retries the SAME
    code and succeeds."""
    code = h.code()
    before = h.journal_len()
    assert h.token(client_secret="wrong", code=code).status == 401
    assert h.journal_len() == before
    assert h.token(client_secret=CLIENT_SECRET, code=code).status == 200


def test_a_refused_pkce_exchange_journals_nothing_and_the_code_survives(h: Harness) -> None:
    code = h.code(code_challenge=CHALLENGE)
    before = h.journal_len()
    assert h.token(code=code, code_verifier="wrong").status == 401
    assert h.journal_len() == before
    assert h.token(code=code, code_verifier=VERIFIER).status == 200


def test_a_refused_refresh_journals_nothing(h: Harness) -> None:
    """A rotated refresh token is dead, but saying so must not write anything:
    the refusal is computed before the rotation write ever could be."""
    first = h.exchange()
    assert h.refresh(refresh_token=first["refresh_token"]).status == 200
    before = h.journal_len()
    assert h.refresh(refresh_token=first["refresh_token"]).status == 401  # reuse
    assert h.refresh(refresh_token="never-issued").status == 401
    assert h.journal_len() == before


# ---------------------------------------------------------------------------
# POST /oauth/v2/refresh -- single-use rotation
# ---------------------------------------------------------------------------


def test_refresh_needs_no_client_secret_and_answers_the_same_four_fields(h: Harness) -> None:
    """{client_id, refresh_token} is the whole documented body
    (https://docs.clover.com/dev/docs/refresh-access-tokens)."""
    first = h.exchange()
    response = h.refresh(refresh_token=first["refresh_token"])
    assert response.status == 200
    body = response.json()
    assert set(body) == {
        "access_token",
        "access_token_expiration",
        "refresh_token",
        "refresh_token_expiration",
    }
    assert body["access_token"] != first["access_token"]
    assert body["refresh_token"] != first["refresh_token"]


def test_a_rotated_refresh_token_is_dead(h: Harness) -> None:
    """DOCUMENTED, verbatim: "Refresh token is for single use and becomes
    invalid immediately after a new access_token and refresh_token pair is
    generated." The area the Square adversarial rounds proved fakes lie
    about, so the reuse is pinned in both directions."""
    first = h.exchange()
    second = h.refresh(refresh_token=first["refresh_token"])
    assert second.status == 200
    reuse = h.refresh(refresh_token=first["refresh_token"])
    assert reuse.status == 401
    assert "single use" in reuse.json()["message"]
    # And the pair the rotation minted works exactly once in its turn.
    assert h.refresh(refresh_token=second.json()["refresh_token"]).status == 200


def test_a_refresh_does_not_end_previously_issued_access_tokens(h: Harness) -> None:
    """JUDGMENT, and the lesson the Square build paid for: Clover documents
    rotation as invalidating the refresh token only, and says nothing about
    prior access tokens -- so they keep working until their own expiry, and a
    consumer must not learn an invalidation rule Clover does not publish."""
    first = h.exchange()
    assert h.refresh(refresh_token=first["refresh_token"]).status == 200
    tokens = h.unit.context.store.collection(COL.tokens)
    record = TokenEntity.from_entity(
        tokens.find(lambda entity: entity.get("access_token") == first["access_token"]) or {}
    )
    assert record.refresh_used_at_ms is not None  # the rotation was recorded
    # The old access token still resolves through the real auth adapter.
    creds = h.unit.context.vendor.auth.credentials(h.unit.context)
    offered = {credential.headers["authorization"] for credential in creds}
    assert f"Bearer {first['access_token']}" in offered


def test_an_expired_refresh_token_is_refused(h: Harness) -> None:
    first = h.exchange()
    tokens = h.unit.context.store.collection(COL.tokens)
    found = tokens.find(lambda entity: entity.get("refresh_token") == first["refresh_token"])
    assert found is not None

    def expire(draft: dict) -> None:  # type: ignore[type-arg]
        draft["refresh_token_expiration_ms"] = 1

    tokens.update(str(found["id"]), expire, silent=True)
    response = h.refresh(refresh_token=first["refresh_token"])
    assert response.status == 401
    assert "expired" in response.json()["message"]


# ---------------------------------------------------------------------------
# Odds and ends
# ---------------------------------------------------------------------------


def test_without_a_merchant_the_error_says_the_seed_is_coming() -> None:
    """A unit nobody seeded cannot mint a code against anybody; the 500 names
    the gap instead of guessing."""
    from tests.unit.clover.harness import Silent
    from vendorfake import create_unit
    from vendorfake.core.transport.inprocess import in_process

    unit = create_unit(vendor="clover", profile="full", logger=Silent())
    try:
        response = in_process(unit).call(method="GET", path="/oauth/v2/authorize", query={"client_id": CLIENT_ID})
        assert response.status == 500
        assert "merchant" in response.json()["message"]
    finally:
        unit.stop()


def test_minted_entities_store_ms_and_the_wire_shows_seconds(h: Harness) -> None:
    """The one conversion in the package, pinned from both sides: the stored
    `_ms` fields are ~1000x the wire values."""
    body = h.exchange()
    tokens = h.unit.context.store.collection(COL.tokens)
    found = tokens.find(lambda entity: entity.get("access_token") == body["access_token"])
    assert found is not None
    record = TokenEntity.from_entity(found)
    assert record.access_token_expiration_ms // 1000 == body["access_token_expiration"]
    assert record.refresh_token_expiration_ms // 1000 == body["refresh_token_expiration"]


def test_stored_codes_carry_the_judgment_ttl(h: Harness) -> None:
    code = h.code()
    record = AuthorizationCodeEntity.from_entity(h.unit.context.store.collection(COL.codes).require(code))
    assert record.merchant_id == MERCHANT_ID
    assert record.client_id == CLIENT_ID
    lifetime_ms = record.expires_at_ms - int(h.unit.context.clock.now())
    assert 0 < lifetime_ms <= 10 * 60 * 1000  # ten minutes, from config
