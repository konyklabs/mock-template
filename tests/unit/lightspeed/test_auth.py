"""Authentication: both grants, rotation, scope, personal tokens, expiry.

Every case here is one the authorization page documents, or one this package
labels JUDGMENT at its site. The two halves of rotation get a test each,
because a consumer who gets one right and the other wrong has a bug that only
shows up in production.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.entities import COL, TokenEntity
from vendorfake.lightspeed.model.auth import TOKEN_TYPE
from vendorfake.lightspeed.seed.constants import (
    SEED_ACCESS_TOKEN,
    SEED_CLIENT_ID,
    SEED_CLIENT_SECRET,
    SEED_DOMAIN_PREFIX,
    SEED_PERSONAL_ACCESS_TOKEN,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_REFRESH_TOKEN,
)

REDIRECT = "https://consumer.example/cb"
CONFIGURED_REDIRECT = "https://consumer.example/callback"
"""``LightspeedConfig.redirect_uri``'s default -- the effective URL an
authorize request that names none is bound to."""
RESPONSE_FIELDS = {
    "access_token",
    "token_type",
    "expires",
    "expires_in",
    "refresh_token",
    "domain_prefix",
    "scope",
}


def _code(h: Harness, **overrides: str) -> str:
    query = {"response_type": "code", "client_id": SEED_CLIENT_ID, "redirect_uri": REDIRECT, "state": "s1"}
    query.update(overrides)
    answered = h.api.get("/connect", query=query)
    assert answered.status == 302, answered.text
    parsed = dict(parse_qsl(urlsplit(answered.headers["location"]).query))
    return parsed["code"]


# -- the authorize stand-in --------------------------------------------------


def test_connect_redirects_with_a_code_and_echoes_the_state(h: Harness) -> None:
    answered = h.api.get(
        "/connect",
        query={"response_type": "code", "client_id": SEED_CLIENT_ID, "redirect_uri": REDIRECT, "state": "abc"},
    )
    assert answered.status == 302
    parsed = dict(parse_qsl(urlsplit(answered.headers["location"]).query))
    assert parsed["state"] == "abc"
    assert parsed["code"]


def test_connect_refuses_an_unknown_client(h: Harness) -> None:
    answered = h.api.get("/connect", query={"client_id": "someone-else", "redirect_uri": REDIRECT})
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "client_id"


def test_connect_refuses_a_scope_the_application_does_not_carry(h: Harness) -> None:
    """``consignments:read`` is a real scope on the vendor's own 58-scope page
    and one this application deliberately never carries: consignments are
    outside issue #94's scoped surface. Named here rather than an invented
    string, so the refusal is about the APPLICATION's grant and not about the
    scope being unrecognisable."""
    answered = h.api.get(
        "/connect",
        query={"client_id": SEED_CLIENT_ID, "redirect_uri": REDIRECT, "scope": "retailer:read consignments:read"},
    )
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "scope"


# -- the authorization_code grant -------------------------------------------


def test_the_exchange_answers_exactly_the_seven_documented_fields(h: Harness) -> None:
    answered = h.exchange(_code(h), redirect_uri=REDIRECT)
    assert answered.status == 200, answered.text
    body = answered.json()
    assert set(body) == RESPONSE_FIELDS
    assert body["token_type"] == TOKEN_TYPE == "Bearer"
    assert body["domain_prefix"] == SEED_DOMAIN_PREFIX
    assert body["expires_in"] == 86400
    assert body["expires"] * 1000 >= body["expires_in"] * 1000


def test_the_minted_token_authenticates(h: Harness) -> None:
    minted = h.exchange(_code(h), redirect_uri=REDIRECT).json()
    answered = h.get(h.path("/retailer"), headers={"authorization": f"Bearer {minted['access_token']}"})
    assert answered.status == 200


def test_a_code_is_single_use(h: Harness) -> None:
    code = _code(h)
    assert h.exchange(code, redirect_uri=REDIRECT).status == 200
    replayed = h.exchange(code, redirect_uri=REDIRECT)
    assert replayed.status == 401
    assert "single use" in replayed.json()["message"]


def test_a_wrong_secret_is_refused_without_naming_which_half(h: Harness) -> None:
    answered = h.exchange(_code(h), client_secret="wrong", redirect_uri=REDIRECT)
    assert answered.status == 401
    assert answered.json()["message"] == "The client credentials are not valid."


def test_a_mismatched_redirect_uri_is_refused(h: Harness) -> None:
    answered = h.exchange(_code(h), redirect_uri="https://attacker.example/cb")
    assert answered.status == 401
    assert answered.json()["unit_error"]["field"] == "redirect_uri"


def test_an_unsupported_grant_names_the_two_that_are(h: Harness) -> None:
    answered = h.token_request(grant_type="client_credentials", client_id=SEED_CLIENT_ID)
    assert answered.status == 422
    supported = answered.json()["unit_error"]["supported"]
    assert supported == ["authorization_code", "refresh_token"]


def test_a_json_body_is_accepted_as_well_as_the_documented_form(h: Harness) -> None:
    """JUDGMENT: the page shows a form-encoded request and says nothing about
    JSON; accepting both fails a consumer on the thing under test rather than
    on a content type."""
    code = _code(h)
    answered = h.api.post(
        "/api/1.0/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": SEED_CLIENT_ID,
            "client_secret": SEED_CLIENT_SECRET,
            "redirect_uri": REDIRECT,
        },
    )
    assert answered.status == 200, answered.text


# -- the refresh_token grant and rotation ------------------------------------


def test_a_refresh_issues_a_new_pair(h: Harness) -> None:
    answered = h.refresh()
    assert answered.status == 200, answered.text
    body = answered.json()
    assert body["access_token"] != SEED_ACCESS_TOKEN
    assert body["refresh_token"] != SEED_REFRESH_TOKEN


def test_a_refresh_revokes_the_access_token_it_was_returned_with(h: Harness) -> None:
    """DOCUMENTED: "Using a refresh token will revoke the access token that was
    returned with it." """
    assert h.get(h.path("/retailer")).status == 200
    assert h.refresh().status == 200
    answered = h.get(h.path("/retailer"))
    assert answered.status == 401
    assert answered.json()["unit_error"]["kind"] == "token_revoked"


def test_a_consumed_refresh_token_is_refused(h: Harness) -> None:
    """DOCUMENTED: "You must save this new refresh token and use it the next
    time." JUDGMENT on the status; 401 is what this document uses."""
    assert h.refresh().status == 200
    replayed = h.refresh()
    assert replayed.status == 401
    assert replayed.json()["unit_error"]["reason"] == "refresh_token_reused"


def test_the_new_refresh_token_works_once(h: Harness) -> None:
    rotated = h.refresh().json()["refresh_token"]
    assert h.refresh(rotated).status == 200


def test_a_refresh_needs_the_client_secret(h: Harness) -> None:
    answered = h.refresh(client_secret="wrong")
    assert answered.status == 401


# -- presenting a credential -------------------------------------------------


def test_a_missing_bearer_is_a_401(h: Harness) -> None:
    answered = h.get(h.path("/retailer"), headers={})
    assert answered.status == 401
    assert answered.json()["unit_error"]["reason"] == "no_authorization_header"


def test_a_non_bearer_scheme_is_a_401(h: Harness) -> None:
    answered = h.get(h.path("/retailer"), headers={"authorization": f"Basic {SEED_ACCESS_TOKEN}"})
    assert answered.status == 401
    assert answered.json()["unit_error"]["reason"] == "not_a_bearer_header"


def test_an_unknown_token_is_a_401(h: Harness) -> None:
    answered = h.get(h.path("/retailer"), headers={"authorization": "Bearer nope"})
    assert answered.status == 401
    assert answered.json()["unit_error"]["reason"] == "unknown_token"


def test_a_missing_scope_is_a_403_naming_it(h: Harness) -> None:
    answered = h.get(h.path("/webhooks"), headers={"authorization": f"Bearer {SEED_READ_ONLY_ACCESS_TOKEN}"})
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["webhooks"]


def test_the_retailer_route_needs_both_of_its_documented_scopes(h: Harness) -> None:
    """``GET /retailer``'s own description names a PAIR:
    ``🔒 Requires: retailer:read payment_types:read scopes``."""
    one_scope = h.restricted_token("retailer:read")
    answered = h.get(h.path("/retailer"), headers=one_scope)
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["payment_types:read"]


def test_a_personal_token_authenticates_identically(h: Harness) -> None:
    """DOCUMENTED: personal tokens are "applied identically to OAuth tokens via
    the Authorization header"."""
    answered = h.get(h.path("/retailer"), headers=h.personal_auth)
    assert answered.status == 200


def test_a_personal_token_never_expires(h: Harness) -> None:
    """The authorization page states no lifetime for one, and an admin creates
    it in the web application; inventing an expiry would teach a rule the
    vendor has not published."""
    stored = h.unit.context.store.collection(COL.tokens).find(
        lambda entity: entity.get("access_token") == SEED_PERSONAL_ACCESS_TOKEN
    )
    assert stored is not None
    assert TokenEntity.from_entity(stored).expires_at_ms is None


def test_an_expired_token_is_a_401_once_the_clock_passes_it(virtual: Harness) -> None:
    """The seeded token's documented-example lifetime is 24 hours; advancing
    the unit's clock past it is what ends it."""
    assert virtual.get(virtual.path("/retailer")).status == 200
    virtual.unit.context.clock.advance(86_400_001)
    answered = virtual.get(virtual.path("/retailer"))
    assert answered.status == 401
    assert answered.json()["unit_error"]["kind"] == "token_expired"


def test_the_published_credentials_drop_a_revoked_token(h: Harness) -> None:
    """``GET /__unit/auth`` is computed from the store, so a token a refresh
    just revoked stops being offered."""
    before = {row["headers"]["authorization"] for row in h.api.get("/__unit/auth").json()["credentials"]}
    assert f"Bearer {SEED_ACCESS_TOKEN}" in before
    assert h.refresh().status == 200
    after = {row["headers"]["authorization"] for row in h.api.get("/__unit/auth").json()["credentials"]}
    assert f"Bearer {SEED_ACCESS_TOKEN}" not in after


# -- the guards nothing used to hold in place --------------------------------


def test_a_code_expires_after_the_configured_ttl(virtual: Harness) -> None:
    """UNPROTECTED UNTIL NOW: deleting the expiry branch left all 419 tests in
    this suite green, so a refactor of the exchange path could drop the only
    thing that makes a ten-minute code stop working and nothing would say so.
    Both sibling vendors pin this; this is the same test for
    ``LightspeedConfig.authorization_code_ttl_ms`` (600000ms, labelled
    JUDGMENT at its site -- the spike figure is unconfirmed)."""
    code = _code(virtual)
    virtual.unit.context.clock.advance(600_001)
    answered = virtual.exchange(code, redirect_uri=REDIRECT)
    assert answered.status == 401
    assert answered.json()["unit_error"]["field"] == "code"
    assert "expired" in answered.json()["message"]


def test_a_code_still_inside_the_ttl_is_exchanged(virtual: Harness) -> None:
    """The other side of the boundary, so the test above is failing for the
    expiry and not for the clock."""
    code = _code(virtual)
    virtual.unit.context.clock.advance(599_000)
    assert virtual.exchange(code, redirect_uri=REDIRECT).status == 200


def test_an_authorize_with_no_redirect_uri_still_binds_the_effective_one(h: Harness) -> None:
    """THE UNGUARDED BRANCH. ``redirect_uri`` is optional on the authorize
    request and falls back to the unit's configured default; the code used to
    record the ABSENCE, and the exchange only compared when it had recorded
    something -- so a code minted this way was redeemable with any
    redirect_uri at all, teaching a consumer a rule Lightspeed does not have.
    """
    answered = h.api.get("/connect", query={"response_type": "code", "client_id": SEED_CLIENT_ID, "state": "s"})
    assert answered.status == 302
    location = urlsplit(answered.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CONFIGURED_REDIRECT
    code = dict(parse_qsl(location.query))["code"]

    stolen = h.exchange(code, redirect_uri="https://attacker.example/steal")
    assert stolen.status == 401
    assert stolen.json()["unit_error"]["field"] == "redirect_uri"
    # And the effective URL is the one that works.
    assert h.exchange(code, redirect_uri=CONFIGURED_REDIRECT).status == 200


def test_a_wrong_client_id_is_refused_on_the_authorization_code_grant(h: Harness) -> None:
    """UNPROTECTED UNTIL NOW: with the branch deleted the whole suite stayed
    green, leaving ``_check_secret`` as the token endpoint's only credential
    check -- so a caller presenting the wrong client_id with the right secret
    was issued a token. A typo'd or stale application id is an ordinary
    misconfiguration, and it must fail here the way it fails in production."""
    answered = h.exchange(_code(h), client_id="not-this-application", redirect_uri=REDIRECT)
    assert answered.status == 401
    assert answered.json()["unit_error"]["field"] == "client_id"


def test_a_wrong_client_id_is_refused_on_the_refresh_grant(h: Harness) -> None:
    """The same guard, reached through the other grant: it sits above the
    branch on ``grant_type``, and a test on one grant alone would not say so."""
    answered = h.refresh(client_id="not-this-application")
    assert answered.status == 401
    assert answered.json()["unit_error"]["field"] == "client_id"
