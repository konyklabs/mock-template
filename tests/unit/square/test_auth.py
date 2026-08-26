"""The auth adapter: three documented 401s, and the one that must not fire.

Every code asserted here appears on
https://developer.squareup.com/docs/build-basics/handling-errors. The detail
string on the plain refusal is Square's own, from its example error body.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.square.harness import APPLICATION_ID, APPLICATION_SECRET, Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import SEED_ACCESS_TOKEN, SEED_MERCHANT_ID

DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("oauth-only")


@pytest.fixture
def virtual() -> Iterator[Harness]:
    yield from build_harness("oauth-only", env={"VENDORFAKE_CLOCK": "virtual"})


def status(h: Harness, header: str | None) -> object:
    headers = {} if header is None else {"authorization": header}
    return h.api.post("/oauth2/token/status", {}, headers=headers)


def test_a_seeded_token_authenticates_without_the_oauth_dance(h: Harness) -> None:
    """The design decision worth preserving verbatim: token validity is not
    gated by the `oauth` capability, so a consumer that does not test the
    dance is not forced to run it."""
    response = h.api.post("/oauth2/token/status", {}, headers=h.auth)
    assert response.status == 200
    assert response.json()["merchant_id"] == SEED_MERCHANT_ID


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Basic abc", "Bearer ", "Client wrong-secret"],
)
def test_a_header_that_is_not_a_bearer_credential_is_unauthorized(h: Harness, header: str | None) -> None:
    response = status(h, header)
    assert response.status == 401
    assert first_error(response)["code"] == "UNAUTHORIZED"


def test_an_unknown_token_gets_squares_own_wording(h: Harness) -> None:
    response = status(h, "Bearer EAAAnot-a-real-token")
    assert response.status == 401
    assert first_error(response)["detail"] == "This request could not be authorized."


def test_a_revoked_token_is_distinguished_from_an_unknown_one(h: Harness) -> None:
    h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID},
        headers=h.client_auth,
    )
    response = status(h, f"Bearer {SEED_ACCESS_TOKEN}")
    assert response.status == 401
    assert first_error(response)["code"] == "ACCESS_TOKEN_REVOKED"


def test_an_expired_token_is_distinguished_from_a_revoked_one(virtual: Harness) -> None:
    virtual.api.post("/__unit/clock/advance", {"ms": 31 * DAY_MS})
    response = status(virtual, f"Bearer {SEED_ACCESS_TOKEN}")
    assert response.status == 401
    assert first_error(response)["code"] == "ACCESS_TOKEN_EXPIRED"


def test_a_credential_keeps_its_inner_spaces(h: Harness) -> None:
    """Only the scheme is split off. A token is opaque, and splitting on every
    space would reject a credential a client had pasted intact -- which is why
    the failure below is "not this token" and not "malformed header"."""
    response = status(h, "Bearer two words")
    assert first_error(response)["detail"] == "This request could not be authorized."


def test_the_client_scheme_accepts_only_the_application_secret(h: Harness) -> None:
    accepted = h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID},
        headers={"authorization": f"Client {APPLICATION_SECRET}"},
    )
    assert accepted.status == 200
    refused = h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID},
        headers={"authorization": f"Bearer {APPLICATION_SECRET}"},
    )
    assert refused.status == 401


def test_the_client_secret_is_read_from_the_profile_not_from_the_default() -> None:
    """The reason the adapter holds the vendor rather than a copy of its
    secret: the profile's `vendor` block resolves at hydrate, after the routes
    and the adapter already exist."""
    for h in build_harness("oauth-only", env={"VENDORFAKE_VENDOR_APPLICATION_SECRET": "sandbox-sq0csb-replaced"}):
        replaced = h.api.post(
            "/oauth2/revoke",
            {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID},
            headers={"authorization": "Client sandbox-sq0csb-replaced"},
        )
        assert replaced.status == 200, replaced.text
        stale = h.api.post(
            "/oauth2/revoke",
            {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID},
            headers={"authorization": f"Client {APPLICATION_SECRET}"},
        )
        assert stale.status == 401
