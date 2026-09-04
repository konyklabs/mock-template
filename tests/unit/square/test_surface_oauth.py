"""The OAuth surface, against the shapes Square documents.

Two rules this file follows and the reference did not:

* **every assertion names its source.** Where Square publishes the behaviour,
  the docstring quotes it; where it does not, the test says the value is this
  unit's convention. That is the difference between a test that pins fidelity
  and a test that pins whatever the code happened to do.
* **the urlencoded path is covered as thoroughly as the JSON one.** The
  reference has zero tests over its own form branch, which is precisely why a
  boolean read as a string went unnoticed there for the life of the file.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.square.harness import (
    APPLICATION_ID,
    APPLICATION_SECRET,
    CONFIGURED_REDIRECT_URI,
    Harness,
    first_error,
)
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.config import DEFAULT_SCOPES
from vendorfake.square.entities import COL, TokenEntity
from vendorfake.square.seed.constants import SEED_LOCATION_ID, SEED_MERCHANT_ID, SEED_OPEN_ORDER_ID

DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("oauth-only")


@pytest.fixture
def virtual() -> Iterator[Harness]:
    """A unit on a virtual clock, so expiry is a call rather than a wait."""
    yield from build_harness("oauth-only", env={"VENDORFAKE_CLOCK": "virtual"})


@pytest.fixture
def full() -> Iterator[Harness]:
    """OAuth *and* Orders, so a minted token can be spent on a real route.

    The scope tests below need both: whether a token was really down-scoped is
    only answerable by making a call the missing scope would have allowed.
    """
    yield from build_harness("full")


def query_of(location: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(location).query)


def pkce_pair(verifier: str) -> tuple[str, str]:
    """``(verifier, challenge)`` for the S256 method.

    Computed here with the standard library rather than by asking the unit, so
    the test is an independent implementation of the thing under test.
    """
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# GET /oauth2/authorize
# ---------------------------------------------------------------------------


def test_authorize_redirects_with_code_response_type_and_state(h: Harness) -> None:
    """ "the authorization page redirects to your redirect URL with the
    authorization code" -- and `state` is echoed back unchanged.
    https://developer.squareup.com/docs/oauth-api/receive-and-manage-tokens
    """
    response = h.authorize(state="unit-test-state", redirect_uri=CONFIGURED_REDIRECT_URI)
    assert response.status == 302
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CONFIGURED_REDIRECT_URI
    params = query_of(response.headers["location"])
    assert params["response_type"] == ["code"]
    assert params["state"] == ["unit-test-state"]
    assert params["code"][0].startswith("sq0cgb-")


def test_a_redirect_carries_a_zero_byte_body_and_no_content_type(h: Harness) -> None:
    """The kernel's precedence rule, observed from outside it: `redirect()`
    returns an empty text body, and an empty text body sends no content type. A
    302 answered as `{}` with `application/json` is what a truthiness test on
    the body would produce, and this is the first thing an OAuth consumer
    touches."""
    response = h.authorize()
    assert response.body == b""
    assert response.header("content-type") is None


def test_authorize_falls_back_to_the_configured_redirect_uri(h: Harness) -> None:
    response = h.authorize()
    assert response.headers["location"].startswith(CONFIGURED_REDIRECT_URI)


def test_a_denial_redirects_with_access_denied(h: Harness) -> None:
    """ "error=access_denied" and "error_description=user_denied".
    https://developer.squareup.com/docs/oauth-api/receive-and-manage-tokens
    """
    response = h.authorize(state="unit-test-state", unit_prompt="deny")
    params = query_of(response.headers["location"])
    assert params["error"] == ["access_denied"]
    assert params["error_description"] == ["user_denied"]
    assert params["state"] == ["unit-test-state"]
    assert "code" not in params


def test_authorize_refuses_an_unknown_client_id(h: Harness) -> None:
    """This unit is configured for exactly one application; naming another is
    a mistake worth reporting rather than a code worth minting."""
    response = h.api.call(method="GET", path="/oauth2/authorize", query={"client_id": "someone-elses-app"})
    assert response.status == 400
    error = first_error(response)
    assert error["field"] == "client_id"
    assert "sandbox-sq0idb-unit-square-application" in error["detail"]


def test_authorize_requires_a_client_id(h: Harness) -> None:
    response = h.api.call(method="GET", path="/oauth2/authorize", query={})
    assert response.status == 400
    assert first_error(response)["field"] == "client_id"


def test_the_default_scope_set_is_the_documented_one(h: Harness) -> None:
    """ "MERCHANT_PROFILE_READ PAYMENTS_READ SETTLEMENTS_READ BANK_ACCOUNTS_READ"
    is what Square grants when `scope` is omitted.
    https://developer.squareup.com/reference/square/oauth-api/authorize
    """
    code = h.code()
    token = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code)
    status = h.api.post(
        "/oauth2/token/status",
        {},
        headers={"authorization": f"Bearer {token.json()['access_token']}"},
    )
    assert status.json()["scopes"] == list(DEFAULT_SCOPES)


def test_scope_splits_on_spaces_and_on_plus(h: Harness) -> None:
    """A consumer who hand-builds the authorize URL percent-decodes to
    `A+B`; one who uses a URL builder produces `A B`. Both mean two scopes."""
    code = h.code(scope="ORDERS_READ+ORDERS_WRITE MERCHANT_PROFILE_READ")
    token = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code)
    status = h.api.post(
        "/oauth2/token/status",
        {},
        headers={"authorization": f"Bearer {token.json()['access_token']}"},
    )
    assert status.json()["scopes"] == ["ORDERS_READ", "ORDERS_WRITE", "MERCHANT_PROFILE_READ"]


def test_the_html_prompt_renders_a_consent_page(h: Harness) -> None:
    """A mock affordance, not a Square document: a human driving the flow in a
    browser needs somewhere to click."""
    response = h.authorize(unit_prompt="html", scope="ORDERS_READ")
    assert response.status == 200
    assert response.header("content-type") == "text/html; charset=utf-8"
    assert "Jet Fuel Coffee" in response.text
    assert "ORDERS_READ" in response.text
    assert "unit_prompt=deny" in response.text


def test_the_consent_page_escapes_what_it_interpolates(h: Harness) -> None:
    """The scope list comes from a query parameter a consumer controls."""
    response = h.authorize(unit_prompt="html", scope="<script>alert(1)</script>")
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


# ---------------------------------------------------------------------------
# POST /oauth2/token -- the authorization_code grant
# ---------------------------------------------------------------------------


def test_the_code_exchange_returns_the_documented_response(h: Harness) -> None:
    """Every field on
    https://developer.squareup.com/reference/square/oauth-api/obtain-token,
    and `expires_at` "in ISO 8601 format" truncated to seconds.
    """
    code = h.code()
    response = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code)
    assert response.status == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["short_lived"] is False
    assert body["merchant_id"] == SEED_MERCHANT_ID
    assert body["access_token"].startswith("EAAA")
    assert body["refresh_token"].startswith("EQAA")
    assert body["expires_at"].endswith("Z")
    assert len(body["expires_at"]) == len("2026-08-25T00:00:00Z")
    # "Refresh tokens obtained using the code flow don't expire", so the key is
    # absent rather than null.
    assert "refresh_token_expires_at" not in body


def test_a_thirty_day_expiry_by_default(virtual: Harness) -> None:
    """ "Square OAuth access tokens expire after 30 days."
    https://developer.squareup.com/docs/oauth-api/overview

    Asserted on a virtual clock by advancing to either side of the boundary,
    which is a stronger statement than arithmetic on a timestamp: it is the
    token's own behaviour, through the auth adapter, that moves.
    """
    code = virtual.code()
    token = virtual.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code).json()
    auth = {"authorization": f"Bearer {token['access_token']}"}

    virtual.api.post("/__unit/clock/advance", {"ms": 29 * DAY_MS})
    assert virtual.api.post("/oauth2/token/status", {}, headers=auth).status == 200

    virtual.api.post("/__unit/clock/advance", {"ms": 2 * DAY_MS})
    expired = virtual.api.post("/oauth2/token/status", {}, headers=auth)
    assert expired.status == 401
    assert first_error(expired)["code"] == "ACCESS_TOKEN_EXPIRED"


def test_short_lived_expires_in_twenty_four_hours(virtual: Harness) -> None:
    """ "Indicates whether the returned access token should expire in 24 hours."
    https://developer.squareup.com/reference/square/oauth-api/obtain-token
    """
    code = virtual.code()
    token = virtual.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=code,
        short_lived=True,
    )
    assert token.json()["short_lived"] is True
    auth = {"authorization": f"Bearer {token.json()['access_token']}"}

    virtual.api.post("/__unit/clock/advance", {"ms": 23 * 60 * 60 * 1000})
    assert virtual.api.post("/oauth2/token/status", {}, headers=auth).status == 200
    virtual.api.post("/__unit/clock/advance", {"ms": 2 * 60 * 60 * 1000})
    assert virtual.api.post("/oauth2/token/status", {}, headers=auth).status == 401


def test_short_lived_arrives_as_a_string_on_the_form_path_and_still_means_true(h: Harness) -> None:
    """The behaviour change this rebuild makes deliberately, and the one the
    reference got wrong on its own untested branch.

    In a urlencoded body every value is a string, so `short_lived=true` reaches
    the handler as `"true"`. The reference tests `body.short_lived === true`,
    so it reads that as false and hands back a 30-day token to a consumer who
    asked for a 24-hour one. Pydantic coerces here, so the two encodings agree.

    Square documents this endpoint as JSON only, so there is no published
    answer to defer to: this is the mock's convention, chosen in the consumer's
    favour, and it is labelled as such in the surface module.
    """
    code = h.code()
    response = h.token_form(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=code,
        short_lived=True,
    )
    assert response.status == 200, response.text
    assert response.json()["short_lived"] is True


def test_the_two_encodings_produce_the_same_response_shape(h: Harness) -> None:
    """Everything but the volatile fields is identical, which is the claim the
    judgment call rests on: the encoding decides nothing except the encoding."""
    volatile = {"access_token", "refresh_token", "expires_at"}

    def shape(body: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in body.items() if key not in volatile}

    json_body = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    form_body = h.token_form(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    assert shape(json_body) == shape(form_body)
    assert set(json_body) == set(form_body)


def test_a_reused_authorization_code_is_refused(h: Harness) -> None:
    """ "The authorization code ... is single use."
    https://developer.squareup.com/docs/oauth-api/overview
    """
    code = h.code()
    fields = {"client_secret": APPLICATION_SECRET, "grant_type": "authorization_code", "code": code}
    assert h.token(**fields).status == 200
    second = h.token(**fields)
    assert second.status == 401
    error = first_error(second)
    assert error["category"] == "AUTHENTICATION_ERROR"
    assert "single use" in error["detail"]


def test_a_reused_code_is_refused_on_the_form_path_too(h: Harness) -> None:
    code = h.code()
    fields = {"client_secret": APPLICATION_SECRET, "grant_type": "authorization_code", "code": code}
    assert h.token_form(**fields).status == 200
    assert h.token_form(**fields).status == 401


def test_a_code_expires_after_five_minutes(virtual: Harness) -> None:
    """ "The authorization code expires 5 minutes after the Square
    authorization page generates the code."
    https://developer.squareup.com/docs/oauth-api/overview
    """
    code = virtual.code()
    virtual.api.post("/__unit/clock/advance", {"ms": 5 * 60 * 1000 + 1000})
    response = virtual.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code)
    assert response.status == 401
    assert first_error(response)["code"] == "UNAUTHORIZED"
    assert "5 minutes" in first_error(response)["detail"]


def test_an_unknown_code_is_refused(h: Harness) -> None:
    response = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code="sq0cgb-nope")
    assert response.status == 401
    assert first_error(response)["field"] == "code"


def test_a_wrong_client_secret_is_refused(h: Harness) -> None:
    response = h.token(client_secret="wrong", grant_type="authorization_code", code=h.code())
    assert response.status == 401
    assert first_error(response)["code"] == "UNAUTHORIZED"
    assert first_error(response)["field"] == "client_secret"


def test_a_missing_client_secret_on_the_code_flow_is_a_missing_field(h: Harness) -> None:
    """ "client_secret ... Required for the `authorization_code` grant type."
    https://developer.squareup.com/reference/square/oauth-api/obtain-token
    """
    response = h.token(grant_type="authorization_code", code=h.code())
    assert response.status == 400
    assert first_error(response)["field"] == "client_secret"


def test_a_client_id_for_another_application_is_refused_before_anything_else(h: Harness) -> None:
    response = h.api.post(
        "/oauth2/token",
        {"client_id": "someone-elses-app", "grant_type": "authorization_code", "code": "irrelevant"},
    )
    assert response.status == 401
    assert first_error(response)["field"] == "client_id"


@pytest.mark.parametrize("missing", ["client_id", "grant_type"])
def test_the_envelope_fields_are_required(h: Harness, missing: str) -> None:
    body = {"client_id": APPLICATION_ID, "grant_type": "authorization_code"}
    del body[missing]
    response = h.api.post("/oauth2/token", body)
    assert response.status == 400
    assert first_error(response)["field"] == missing


def test_an_empty_string_is_the_same_failure_as_an_absent_field(h: Harness) -> None:
    """A urlencoded `grant_type=` parses to the empty string, not to a missing
    key, and the reference's `requireString` rejects both the same way."""
    response = h.api.call(
        method="POST",
        path="/oauth2/token",
        headers={"content-type": "application/x-www-form-urlencoded"},
        raw_body=f"client_id={APPLICATION_ID}&grant_type=".encode(),
    )
    assert response.status == 400
    assert first_error(response)["field"] == "grant_type"


def test_an_unsupported_grant_type_lists_what_is_supported(h: Harness) -> None:
    response = h.token(client_secret=APPLICATION_SECRET, grant_type="client_credentials")
    assert response.status == 400
    error = first_error(response)
    assert error["code"] == "INVALID_VALUE"
    assert error["field"] == "grant_type"
    assert "authorization_code, refresh_token" in error["detail"]
    assert response.json()["unit_error"]["supported"] == ["authorization_code", "refresh_token"]


def test_the_documented_migration_token_grant_is_named_as_a_shrink() -> None:
    """Square documents three grant types; this unit implements two. The third
    is enumerated rather than forgotten, so the omission reads as a decision.
    https://developer.squareup.com/reference/square/oauth-api/obtain-token
    """
    from vendorfake.square.model.oauth import SQUARE_GRANT_TYPES, SUPPORTED_GRANT_TYPES

    assert SQUARE_GRANT_TYPES == ("authorization_code", "refresh_token", "migration_token")
    assert set(SUPPORTED_GRANT_TYPES) < set(SQUARE_GRANT_TYPES)


# ---------------------------------------------------------------------------
# redirect_uri -- the second unlabelled divergence in the reference
# ---------------------------------------------------------------------------


def test_redirect_uri_must_match_when_the_authorize_request_supplied_one(h: Harness) -> None:
    """ "redirect_uri ... Required if provided in the authorization URL."
    https://developer.squareup.com/reference/square/oauth-api/obtain-token

    The reference stored this on the code entity and never looked at it again,
    so a consumer testing the mismatch case got a false pass -- on the one
    attack the parameter exists to prevent.
    """
    code = h.code(redirect_uri=CONFIGURED_REDIRECT_URI)
    response = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=code,
        redirect_uri="https://attacker.test/callback",
    )
    assert response.status == 401
    assert first_error(response)["field"] == "redirect_uri"


def test_redirect_uri_is_required_when_the_authorize_request_supplied_one(h: Harness) -> None:
    code = h.code(redirect_uri=CONFIGURED_REDIRECT_URI)
    response = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code)
    assert response.status == 400
    assert first_error(response)["field"] == "redirect_uri"


def test_a_matching_redirect_uri_is_accepted(h: Harness) -> None:
    code = h.code(redirect_uri=CONFIGURED_REDIRECT_URI)
    response = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=code,
        redirect_uri=CONFIGURED_REDIRECT_URI,
    )
    assert response.status == 200, response.text


def test_redirect_uri_is_not_required_when_the_authorize_request_omitted_it(h: Harness) -> None:
    """Square's sentence stops at "if provided in the authorization URL". A
    code issued against the unit's configured default was not provided one, so
    nothing is required at exchange -- and absence is recorded as absence
    rather than as the fallback value."""
    code = h.code()
    assert h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code).status == 200


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def test_pkce_exchanges_without_a_client_secret_and_carries_a_refresh_expiry(h: Harness) -> None:
    """ "Refresh tokens obtained using the PKCE flow are single-use tokens and
    expire after 90 days."  https://developer.squareup.com/docs/oauth-api/overview
    """
    verifier, challenge = pkce_pair("unit-test-code-verifier-0123456789abcdef")
    code = h.code(code_challenge=challenge)
    response = h.token(grant_type="authorization_code", code=code, code_verifier=verifier)
    assert response.status == 200, response.text
    assert response.json()["refresh_token_expires_at"].endswith("Z")


def test_pkce_refuses_a_wrong_verifier(h: Harness) -> None:
    _, challenge = pkce_pair("the-real-verifier")
    code = h.code(code_challenge=challenge)
    response = h.token(grant_type="authorization_code", code=code, code_verifier="not-the-verifier")
    assert response.status == 401
    assert first_error(response)["field"] == "code_verifier"


def test_pkce_requires_a_verifier(h: Harness) -> None:
    _, challenge = pkce_pair("the-real-verifier")
    code = h.code(code_challenge=challenge)
    response = h.token(grant_type="authorization_code", code=code)
    assert response.status == 400
    assert first_error(response)["field"] == "code_verifier"


def test_pkce_works_over_a_form_encoded_body_too(h: Harness) -> None:
    verifier, challenge = pkce_pair("form-encoded-pkce-verifier")
    code = h.code(code_challenge=challenge)
    response = h.token_form(grant_type="authorization_code", code=code, code_verifier=verifier)
    assert response.status == 200, response.text


def test_a_pkce_refresh_issues_a_new_single_use_refresh_token(h: Harness) -> None:
    """The PKCE half of the refresh rule, which this build keeps: the refresh
    token is single use, so the old record is retired and its access token goes
    with it."""
    verifier, challenge = pkce_pair("pkce-refresh-verifier")
    code = h.code(code_challenge=challenge)
    first = h.token(grant_type="authorization_code", code=code, code_verifier=verifier).json()

    refreshed = h.token(grant_type="refresh_token", refresh_token=first["refresh_token"])
    assert refreshed.status == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != first["refresh_token"]

    reused = h.token(grant_type="refresh_token", refresh_token=first["refresh_token"])
    assert reused.status == 401
    assert "revoked" in first_error(reused)["detail"]


# ---------------------------------------------------------------------------
# `scopes`: the intersection rule
#
# One sentence governs every test in this section: "The returned access token
# is limited to the permissions that are the intersection of these requested
# permissions and those authorized by the provided `refresh_token`."
# https://developer.squareup.com/reference/square/oauth-api/obtain-token
# ---------------------------------------------------------------------------


def scopes_of(h: Harness, access_token: str) -> list[str]:
    """What a minted token actually carries, read back through the API."""
    response = h.api.post("/oauth2/token/status", {}, headers={"authorization": f"Bearer {access_token}"})
    assert response.status == 200, response.text
    return list(response.json()["scopes"])


def test_a_code_exchange_cannot_ask_for_more_than_the_seller_approved(h: Harness) -> None:
    """The escalation, on the exchange path. The seller approved ORDERS_READ;
    the exchange asks for four permissions and gets the one that is in both
    sets."""
    minted = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(scope="ORDERS_READ"),
        scopes=["ORDERS_READ", "ORDERS_WRITE", "PAYMENTS_WRITE", "BANK_ACCOUNTS_READ"],
    )
    assert minted.status == 200, minted.text
    assert scopes_of(h, minted.json()["access_token"]) == ["ORDERS_READ"]


def test_a_refresh_cannot_ask_for_more_than_the_refresh_token_authorizes(h: Harness) -> None:
    """The same rule on the refresh path, which is the one Square's sentence
    is literally about."""
    first = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(scope="ORDERS_READ"),
    ).json()
    refreshed = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
        scopes=["ORDERS_READ", "ORDERS_WRITE"],
    )
    assert refreshed.status == 200, refreshed.text
    assert scopes_of(h, refreshed.json()["access_token"]) == ["ORDERS_READ"]


def test_scopes_narrow_a_grant_which_is_the_documented_direction(h: Harness) -> None:
    """ "you can create a new access token that has a reduced set of
    permissions from the ones granted when the seller approved your
    authorization."
    https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope
    """
    first = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(scope="ORDERS_READ ORDERS_WRITE MERCHANT_PROFILE_READ"),
    ).json()
    assert scopes_of(h, first["access_token"]) == ["ORDERS_READ", "ORDERS_WRITE", "MERCHANT_PROFILE_READ"]

    reduced = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
        scopes=["ORDERS_READ"],
    )
    assert scopes_of(h, reduced.json()["access_token"]) == ["ORDERS_READ"]


def test_omitting_scopes_keeps_the_whole_grant(h: Harness) -> None:
    """Absent is "no opinion", not an empty intersection: the token carries
    everything the seller approved."""
    first = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(scope="ORDERS_READ ORDERS_WRITE"),
    ).json()
    assert scopes_of(h, first["access_token"]) == ["ORDERS_READ", "ORDERS_WRITE"]
    refreshed = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
    ).json()
    assert scopes_of(h, refreshed["access_token"]) == ["ORDERS_READ", "ORDERS_WRITE"]


def test_an_intersection_with_nothing_in_it_is_refused(h: Harness) -> None:
    """JUDGMENT, and the test says so: Square publishes no error table for this
    endpoint at all. Minting a token with an empty scope list would answer 200
    and then 403 every call made with it, so the request that is wrong is
    refused instead."""
    response = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(scope="ORDERS_READ"),
        scopes=["PAYMENTS_WRITE"],
    )
    assert response.status == 400
    assert first_error(response)["field"] == "scopes"


def test_a_down_scoped_token_really_loses_write_access(full: Harness) -> None:
    """The finding with a security shape, end to end.

    A consumer's test asks "does my down-scoped token actually lose write
    access?" While `scopes` REPLACED the grant, this passed against the fake --
    the token came back with ORDERS_WRITE on it -- and failed against Square,
    where the intersection is empty of it. The 403 is the point: the fake has
    to be the first thing that fails, not production.
    """
    minted = full.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=full.code(scope="ORDERS_READ"),
        scopes=["ORDERS_READ", "ORDERS_WRITE"],
    )
    assert minted.status == 200, minted.text
    token = minted.json()["access_token"]
    assert scopes_of(full, token) == ["ORDERS_READ"]

    header = {"authorization": f"Bearer {token}"}
    assert full.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=header).status == 200
    created = full.api.post("/v2/orders", {"order": {"location_id": SEED_LOCATION_ID}}, headers=header)
    assert created.status == 403, created.text


# ---------------------------------------------------------------------------
# The code-flow refresh, and the correction this rebuild makes
# ---------------------------------------------------------------------------


def test_a_code_flow_refresh_returns_the_same_refresh_token(h: Harness) -> None:
    """`"refresh_token": "<SAME REFRESH TOKEN AS REQUEST>"` for the code flow.
    https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope
    """
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    refreshed = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
    )
    assert refreshed.status == 200, refreshed.text
    assert refreshed.json()["refresh_token"] == first["refresh_token"]
    assert refreshed.json()["access_token"] != first["access_token"]


def test_a_code_flow_refresh_leaves_the_previous_access_token_working(h: Harness) -> None:
    """The correction. "A refresh token obtained using the code flow can be
    used to get multiple active access tokens."
    https://developer.squareup.com/docs/oauth-api/overview

    The reference set `revokedAt` on the previous record on every refresh, so
    its prior access token answered 401 with ACCESS_TOKEN_REVOKED -- a
    token-invalidation rule Square does not have, and one a consumer only
    discovers is absent in production, where their re-auth path never runs.
    """
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
    )
    still_valid = h.api.post(
        "/oauth2/token/status",
        {},
        headers={"authorization": f"Bearer {first['access_token']}"},
    )
    assert still_valid.status == 200, still_valid.text
    assert still_valid.json()["merchant_id"] == SEED_MERCHANT_ID


def test_a_second_refresh_mints_from_the_second_record_not_the_first(h: Harness) -> None:
    """The consequence of dropping the revoke, and why `superseded_at` exists.

    Code flow returns the same refresh-token string, so two live records now
    share it. `Collection.find` answers in insertion order, so without a filter
    the second refresh would find the *stale* record and mint from its scopes
    and its flow. The older record is marked superseded by a silent write --
    no version bump, no journal entry, no webhook -- and the lookup filters on
    it, while the older access token stays valid.

    WHAT THIS TEST USED TO ASSERT, AND WHY IT WAS WRONG. It authorized
    `ORDERS_READ` alone, refreshed asking for `["ORDERS_READ","ORDERS_WRITE"]`,
    and asserted the minted token carried BOTH -- pinning a privilege
    escalation as correct behaviour. Square documents the opposite in one
    sentence: "The returned access token is limited to the permissions that are
    the intersection of these requested permissions and those authorized by the
    provided `refresh_token`."
    https://developer.squareup.com/reference/square/oauth-api/obtain-token

    A test that pins a defect is worse than no test: this one would have failed
    the fix. The record-supersession behaviour it is really about is asserted
    here with a scope change that *narrows*, which is the direction Square
    permits -- and the narrowing is what makes the second mint distinguishable
    from the first at all.
    """
    first = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(scope="ORDERS_READ ORDERS_WRITE"),
    ).json()
    # `short_lived` is set on refresh and never cleared by it, so it is the
    # property that distinguishes the two records. Scopes used to serve here,
    # and cannot any more: a refresh naming none intersects against the
    # SELLER'S APPROVAL rather than the previous token, so the third mint
    # legitimately carries both again. That coupling hid the ratchet defect --
    # this assertion passed precisely because the grant had been narrowed
    # permanently.
    second = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
        short_lived=True,
    ).json()
    assert second["short_lived"] is True
    third = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
    )
    assert third.status == 200, third.text

    # Inherited from the SECOND record: proof the lookup skipped the superseded
    # one. Had it found the stale first record, short_lived would be false.
    assert third.json()["short_lived"] is True
    assert second["access_token"] != third.json()["access_token"]

    # And the very first access token is still good.
    assert (
        h.api.post("/oauth2/token/status", {}, headers={"authorization": f"Bearer {first['access_token']}"}).status
        == 200
    )


def test_supersession_is_silent(h: Harness) -> None:
    """No version bump and no journal entry, which is what makes it invisible
    to the webhook dispatcher: the journal is the event source, so a silent
    write is the only way to change an entity without emitting an event."""
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    before = len(h.api.get("/__unit/journal").json()["entries"])
    h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
    )
    entries = h.api.get("/__unit/journal").json()["entries"]
    # Exactly one new entry: the insert of the new token. The supersession of
    # the old one journals nothing.
    assert len(entries) == before + 1
    assert entries[-1]["op"] == "insert"
    assert entries[-1]["collection"] == COL.tokens

    stored = [
        entity
        for entity in h.unit.context.store.collection(COL.tokens).all()
        if entity["access_token"] == first["access_token"]
    ]
    assert len(stored) == 1
    token = TokenEntity.from_entity(stored[0])
    assert token.superseded_at is not None
    assert token.revoked_at is None
    # The store's version, unmoved: a silent write bumps nothing, which is what
    # `version` records and what an optimistic-concurrency consumer reads.
    assert stored[0]["version"] == 1


def test_short_lived_can_be_set_on_refresh_but_never_cleared(h: Harness) -> None:
    """The reference's asymmetry, preserved: `short_lived` merges as
    `requested or existing`. Typing it `bool | None` and testing `is not None`
    would silently add a path that turns a 24-hour token back into a 30-day
    one."""
    first = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(),
        short_lived=True,
    ).json()
    refreshed = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
        short_lived=False,
    )
    assert refreshed.json()["short_lived"] is True


def test_a_refresh_requires_the_client_secret_on_the_code_flow(h: Harness) -> None:
    """ "client_secret ... required on code-flow refresh, absent on PKCE."
    https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope
    """
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    response = h.token(grant_type="refresh_token", refresh_token=first["refresh_token"])
    assert response.status == 400
    assert first_error(response)["field"] == "client_secret"


def test_an_unknown_refresh_token_is_refused(h: Harness) -> None:
    response = h.token(client_secret=APPLICATION_SECRET, grant_type="refresh_token", refresh_token="EQAAnope")
    assert response.status == 401
    assert first_error(response)["field"] == "refresh_token"


def test_a_refresh_over_a_form_encoded_body(h: Harness) -> None:
    first = h.token_form(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    refreshed = h.token_form(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
    )
    assert refreshed.status == 200, refreshed.text
    assert refreshed.json()["refresh_token"] == first["refresh_token"]


def test_a_scopes_value_that_is_not_an_array_is_ignored(h: Harness) -> None:
    """The reference's `Array.isArray(body.scopes)` gate, preserved rather than
    tightened. It matters most on the form path, where `scopes=ORDERS_READ`
    arrives as a bare string: falling back to the scopes already on the record
    is what the reference does, and refusing the request instead would make the
    mock's own affordance the thing that broke."""
    first = h.token_form(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=h.code(scope="ORDERS_READ ORDERS_WRITE"),
    ).json()
    refreshed = h.token_form(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
        scopes="PAYMENTS_WRITE",
    )
    status = h.api.post(
        "/oauth2/token/status",
        {},
        headers={"authorization": f"Bearer {refreshed.json()['access_token']}"},
    )
    assert status.json()["scopes"] == ["ORDERS_READ", "ORDERS_WRITE"]


# ---------------------------------------------------------------------------
# POST /oauth2/revoke
# ---------------------------------------------------------------------------


def test_revoke_needs_the_client_application_secret_scheme(h: Harness) -> None:
    """ "Authorization: Client APPLICATION_SECRET".
    https://developer.squareup.com/reference/square/oauth-api/revoke-token
    """
    response = h.api.post("/oauth2/revoke", {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID})
    assert response.status == 401


def test_revoke_only_access_token_ends_one_token(h: Harness) -> None:
    """ "terminates only the single token without ending the full
    authorization."  https://developer.squareup.com/reference/square/oauth-api/revoke-token
    """
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    second = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()

    revoked = h.api.post(
        "/oauth2/revoke",
        {
            "client_id": APPLICATION_ID,
            "access_token": first["access_token"],
            "revoke_only_access_token": True,
        },
        headers=h.client_auth,
    )
    assert revoked.status == 200
    assert revoked.json() == {"success": True}

    gone = h.api.post("/oauth2/token/status", {}, headers={"authorization": f"Bearer {first['access_token']}"})
    assert gone.status == 401
    assert first_error(gone)["code"] == "ACCESS_TOKEN_REVOKED"
    assert (
        h.api.post(
            "/oauth2/token/status",
            {},
            headers={"authorization": f"Bearer {second['access_token']}"},
        ).status
        == 200
    )


def test_revoke_by_merchant_ends_the_whole_authorization(h: Harness) -> None:
    """The default: "revoking the entire authorization for you to act on the
    behalf of a seller".
    https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope
    """
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    second = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    response = h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID},
        headers=h.client_auth,
    )
    assert response.status == 200
    for token in (first, second):
        gone = h.api.post("/oauth2/token/status", {}, headers={"authorization": f"Bearer {token['access_token']}"})
        assert gone.status == 401
        assert first_error(gone)["code"] == "ACCESS_TOKEN_REVOKED"


def test_revoke_also_reaches_a_superseded_token(h: Harness) -> None:
    """A superseded record still holds a live access token -- that is the whole
    point of dropping the revoke-on-refresh -- so revoking the authorization
    must end it too. Filtering by merchant and client is what makes that true
    without a special case."""
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=first["refresh_token"],
    )
    h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "merchant_id": SEED_MERCHANT_ID},
        headers=h.client_auth,
    )
    gone = h.api.post("/oauth2/token/status", {}, headers={"authorization": f"Bearer {first['access_token']}"})
    assert gone.status == 401
    assert first_error(gone)["code"] == "ACCESS_TOKEN_REVOKED"


def test_revoke_refuses_both_selectors_at_once(h: Harness) -> None:
    """`access_token` and `merchant_id` are documented as mutually exclusive."""
    response = h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "access_token": "EAAAx", "merchant_id": SEED_MERCHANT_ID},
        headers=h.client_auth,
    )
    assert response.status == 400
    assert first_error(response)["field"] == "merchant_id"


def test_revoke_requires_one_selector(h: Harness) -> None:
    response = h.api.post("/oauth2/revoke", {"client_id": APPLICATION_ID}, headers=h.client_auth)
    assert response.status == 400
    assert first_error(response)["field"] == "access_token"


def test_revoke_never_reports_success_over_a_revocation_that_did_not_happen(h: Harness) -> None:
    """`success` is documented as "If the request is successful, this is
    `true`" and nothing else on the page describes a failure
    (https://developer.squareup.com/reference/square/oauth-api/revoke-token),
    so it must not be `true` over a request that revoked nothing.

    A `client_id` with a typo in it used to answer `{"success": true}` while
    the token went on working: a green test and a live credential. The 401 is
    this unit's convention -- Square publishes no error table here -- and is
    the same one `obtain_token` already answers for a `client_id` that is not
    this application.
    """
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    header = {"authorization": f"Bearer {first['access_token']}"}
    assert h.api.post("/oauth2/token/status", {}, headers=header).status == 200

    refused = h.api.post(
        "/oauth2/revoke",
        {"client_id": "sq0idb-someone-elses-application", "merchant_id": SEED_MERCHANT_ID},
        headers=h.client_auth,
    )
    assert refused.status == 401
    assert first_error(refused)["field"] == "client_id"
    # The token is the assertion that matters: a consumer testing "my revoke
    # call works" against a typo'd application id must not get a green test.
    assert h.api.post("/oauth2/token/status", {}, headers=header).status == 200


def test_revoke_refuses_a_merchant_this_application_holds_no_token_for(h: Harness) -> None:
    """The other half of the same rule, and the same 401 the `access_token`
    branch already answered for a token this application did not issue."""
    response = h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "merchant_id": "MLQNOBODY"},
        headers=h.client_auth,
    )
    assert response.status == 401
    assert first_error(response)["field"] == "merchant_id"


def test_revoke_refuses_a_token_this_application_did_not_issue(h: Harness) -> None:
    response = h.api.post(
        "/oauth2/revoke",
        {"client_id": APPLICATION_ID, "access_token": "EAAAsomeone-elses"},
        headers=h.client_auth,
    )
    assert response.status == 401


def test_revoke_over_a_form_encoded_body(h: Harness) -> None:
    """The one place the form path changes behaviour beyond parsing: a boolean
    flag. `revoke_only_access_token=true` must mean what it says."""
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    second = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    response = h.api.call(
        method="POST",
        path="/oauth2/revoke",
        headers={"content-type": "application/x-www-form-urlencoded", **h.client_auth},
        raw_body=(
            f"client_id={APPLICATION_ID}&access_token={first['access_token']}&revoke_only_access_token=true"
        ).encode(),
    )
    assert response.status == 200, response.text
    assert (
        h.api.post(
            "/oauth2/token/status",
            {},
            headers={"authorization": f"Bearer {second['access_token']}"},
        ).status
        == 200
    )


def test_revoking_twice_is_idempotent_and_journals_once(h: Harness) -> None:
    """A record already revoked is skipped, so a retried revocation does not
    bump versions or emit a second event."""
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
    body = {
        "client_id": APPLICATION_ID,
        "access_token": first["access_token"],
        "revoke_only_access_token": True,
    }
    h.api.post("/oauth2/revoke", body, headers=h.client_auth)
    before = len(h.api.get("/__unit/journal").json()["entries"])
    assert h.api.post("/oauth2/revoke", body, headers=h.client_auth).status == 200
    assert len(h.api.get("/__unit/journal").json()["entries"]) == before


# ---------------------------------------------------------------------------
# POST /oauth2/token/status
# ---------------------------------------------------------------------------


def test_token_status_reports_the_seeded_token(h: Harness) -> None:
    """Token validity is not gated by the `oauth` capability, which is why a
    seeded token authenticates here without the dance ever running."""
    response = h.api.post("/oauth2/token/status", {}, headers=h.auth)
    assert response.status == 200
    body = response.json()
    assert "ORDERS_WRITE" in body["scopes"]
    assert body["merchant_id"] == SEED_MERCHANT_ID
    assert body["client_id"] == APPLICATION_ID


def test_token_status_needs_a_bearer_credential(h: Harness) -> None:
    assert h.api.post("/oauth2/token/status", {}).status == 401
    assert h.api.post("/oauth2/token/status", {}, headers={"authorization": "Client x"}).status == 401


# ---------------------------------------------------------------------------
# The capability gate
# ---------------------------------------------------------------------------


def test_the_oauth_routes_disappear_with_the_capability() -> None:
    """`orders-only` serves no OAuth surface, and says so with a 501 naming the
    capability rather than a 404 that looks like a typo."""
    for harness_ in build_harness("orders-only"):
        response = harness_.api.call(method="GET", path="/oauth2/authorize", query={"client_id": APPLICATION_ID})
        assert response.status == 501
        assert response.header("x-unit-capability") == "oauth"


# ---------------------------------------------------------------------------
# A refusal must not spend the grant it refused
# ---------------------------------------------------------------------------


def test_a_refused_scope_intersection_leaves_the_code_spendable(h: Harness) -> None:
    """Found by an adversarial re-attack, as a regression introduced by the fix
    for the scope-escalation defect itself.

    `_narrowed_scopes` raises when the intersection is empty, and it was being
    evaluated as an argument to `_mint` -- so Python ran it only after the code
    had already been marked used. The consumer received a 400 saying nothing
    had happened, and their next correct attempt received a 401, because the
    code was spent by the request that failed.

    Every other refusal on this endpoint is ordered before the write. This one
    is too, now. The general rule -- a 4xx must not leave a mutation behind --
    is asserted for the whole surface by the conformance suite.
    """
    code = h.code(scope="ORDERS_READ")

    refused = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="authorization_code",
        code=code,
        scopes=["PAYMENTS_WRITE"],
    )
    assert refused.status == 400, refused.text

    accepted = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code)
    assert accepted.status == 200, accepted.text  # the refused request must not have spent the code
    assert accepted.json()["access_token"]


def test_a_down_scoped_refresh_does_not_ratchet_the_grant_down_permanently(h: Harness) -> None:
    """Narrowing is per-token, not per-grant. The reviewer's exact scenario.

    A refresh intersects the request against what the SELLER APPROVED, not
    against what the current token happens to carry. Square narrows "from the
    ones granted when the seller approved", so taking a narrow token for one
    subtask must not stop the grant producing a full one afterwards.

    Intersecting against ``existing.scopes`` made every narrowing permanent,
    with no path back. It is the mirror image of the escalation this surface
    was already fixed for -- wrong in the other direction, and it reads as safe
    because it refuses rather than over-grants, which is exactly why it
    survived a review that was looking for over-granting.
    """
    code = h.code(scope="ORDERS_READ ORDERS_WRITE")
    first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=code).json()
    refresh_token = first["refresh_token"]

    narrowed = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=refresh_token,
        scopes=["ORDERS_READ"],
    )
    assert narrowed.status == 200, narrowed.text

    widened = h.token(
        client_secret=APPLICATION_SECRET,
        grant_type="refresh_token",
        refresh_token=refresh_token,
        scopes=["ORDERS_WRITE"],
    )
    assert widened.status == 200, (
        f"a permission the seller approved became unreachable after one down-scoped refresh: {widened.text}"
    )

    granted = h.api.post(
        "/oauth2/token/status",
        {},
        headers={"authorization": f"Bearer {widened.json()['access_token']}"},
    )
    assert granted.status == 200, granted.text
    assert "ORDERS_WRITE" in granted.json()["scopes"], granted.text


# ---------------------------------------------------------------------------
# VENDORFAKE_CLOCK_START (konyklabs/roadmap#71, D1)
# ---------------------------------------------------------------------------


def test_clock_start_makes_a_refresh_s_documented_access_token_lifetime_reproducible_across_units() -> None:
    """DOCUMENTED: "otherwise 30 days" -- the module docstring's citation for
    the default (non-short-lived) access-token lifetime,
    https://developer.squareup.com/docs/oauth-api/overview. On a virtual clock
    frozen at `clock_start`, a code-flow refresh's `expires_at` is exactly
    `start + 30 days` -- truncated to seconds, Square's own format -- and two
    independently built units on the same `clock_start` agree on it to the
    second, which a wall-clock start instant cannot promise run to run.
    """
    start = "2026-01-01T00:00:00Z"
    expected = "2026-01-31T00:00:00Z"  # start + 30 days, no time elapsed on a frozen virtual clock
    env = {"VENDORFAKE_CLOCK": "virtual", "VENDORFAKE_CLOCK_START": start}

    seen: list[str] = []
    for _ in range(2):  # two independently built units
        for h in build_harness("oauth-only", env=env):
            first = h.token(client_secret=APPLICATION_SECRET, grant_type="authorization_code", code=h.code()).json()
            refreshed = h.token(
                client_secret=APPLICATION_SECRET,
                grant_type="refresh_token",
                refresh_token=first["refresh_token"],
            )
            assert refreshed.status == 200, refreshed.text
            seen.append(refreshed.json()["expires_at"])

    assert seen == [expected, expected]
