"""Clover's webhook authentication: one static header, verified independently.

The rule from the Square signer tests holds here with less to write: the
documented scheme is "send the auth code in ``X-Clover-Auth``", so the
independent check is a string comparison and the interesting assertions are
the *negative* ones -- that nothing else moves the header, and that the one
delivery documented as unauthenticated (the verification POST) carries none.

    https://docs.clover.com/dev/docs/webhooks
"""

from __future__ import annotations

import pytest

from vendorfake.clover.events import VERIFICATION_EVENT_TYPE, verification_event_id
from vendorfake.clover.signer import (
    AUTH_HEADER,
    CLOVER_SIGNER_PROPERTIES,
    CloverWebhookSigner,
    verify_clover_auth,
)
from vendorfake.clover.vendor import create_clover_vendor
from vendorfake.core.kernel.types import PreparedEvent, SignInput

URL = "https://example.test/hooks"
OTHER_URL = "https://elsewhere.test/hooks"
CODE = "3f6a2b1c-0000-4000-8000-000000000001"
OTHER_CODE = "3f6a2b1c-0000-4000-8000-000000000002"
BODY = b'{"appId":"UNITCLOVERAPP","merchants":{}}'
OTHER_BODY = b'{"appId":"UNITCLOVERAPP","merchants":{"M":[]}}'


def sign_input(
    *,
    url: str = URL,
    secret: str = CODE,
    body: bytes = BODY,
    attempt: int = 1,
    event_type: str = "O:CREATE",
    event_id: str = "e1",
) -> SignInput:
    return SignInput(
        notification_url=url,
        raw_body=body,
        secret=secret,
        attempt=attempt,
        event=PreparedEvent(
            type=event_type,
            event_id=event_id,
            entity_id="GHIVJT2ABCRSC",
            created_at="2026-01-01T00:00:00.000Z",
            body={},
        ),
    )


@pytest.fixture
def signer() -> CloverWebhookSigner:
    signed = create_clover_vendor().signer
    assert isinstance(signed, CloverWebhookSigner)
    return signed


# ---------------------------------------------------------------------------
# The scheme.
# ---------------------------------------------------------------------------


def test_the_header_is_the_documented_name_carrying_the_auth_code_verbatim(signer: CloverWebhookSigner) -> None:
    """ "Clover sends the auth code in every message header after the webhook
    callback URL is validated" -- no digest, no encoding, the code itself."""
    assert signer.sign(sign_input()) == {"X-Clover-Auth": CODE}
    assert AUTH_HEADER == "X-Clover-Auth"


def test_sign_returns_exactly_one_header(signer: CloverWebhookSigner) -> None:
    """Everything else a delivery carries comes from `headers()`; a second
    key here would silently take precedence over the header provider."""
    assert list(signer.sign(sign_input())) == [AUTH_HEADER]


def test_the_signer_declares_secret_bound_and_nothing_else(signer: CloverWebhookSigner) -> None:
    """A static scheme is conformant *because* it declares itself static: the
    conformance suite checks each direction as declared."""
    assert signer.properties is CLOVER_SIGNER_PROPERTIES
    assert signer.properties.secret_bound is True
    assert signer.properties.url_bound is False
    assert signer.properties.body_bound is False
    assert signer.properties.signature_headers == ("x-clover-auth",)


def test_each_declared_direction_actually_holds(signer: CloverWebhookSigner) -> None:
    """One input varied at a time. The secret moves the header; the URL, the
    body and the attempt do not -- which is what makes a redelivery verify
    with the same code path as the first send."""
    base = signer.sign(sign_input())[AUTH_HEADER]
    assert signer.sign(sign_input(secret=OTHER_CODE))[AUTH_HEADER] != base
    assert signer.sign(sign_input(url=OTHER_URL))[AUTH_HEADER] == base
    assert signer.sign(sign_input(body=OTHER_BODY))[AUTH_HEADER] == base
    assert signer.sign(sign_input(attempt=6))[AUTH_HEADER] == base


def test_the_units_own_verification_post_carries_no_auth_header(signer: CloverWebhookSigner) -> None:
    """JUDGMENT: the auth code is documented as sent *after* the callback URL
    is validated and the doc says nothing about the verification POST; this
    unit reads "after" as "not before". The unit's own verification is the
    one whose id the surface minted for it."""
    own = sign_input(event_type=VERIFICATION_EVENT_TYPE, event_id=verification_event_id("GHIVJT2ABCRSC"))
    assert signer.sign(own) == {}


def test_the_verification_type_alone_does_not_drop_the_header(signer: CloverWebhookSigner) -> None:
    """`POST /__unit/webhooks/emit` can name any type but not this id, so an
    emitted 'verification' event is signed like everything else. Without the
    id check the emitter would be a way to send unauthenticated deliveries."""
    forged = sign_input(event_type=VERIFICATION_EVENT_TYPE, event_id="e1")
    assert signer.sign(forged) == {AUTH_HEADER: CODE}
    wrong_entity = sign_input(event_type=VERIFICATION_EVENT_TYPE, event_id=verification_event_id("SOMEONEELSE00"))
    assert signer.sign(wrong_entity) == {AUTH_HEADER: CODE}


def test_signing_is_deterministic(signer: CloverWebhookSigner) -> None:
    assert signer.sign(sign_input()) == signer.sign(sign_input())


# ---------------------------------------------------------------------------
# The consumer-side helper.
# ---------------------------------------------------------------------------


def test_the_verifier_round_trips_what_the_signer_produced(signer: CloverWebhookSigner) -> None:
    headers = dict(signer.sign(sign_input()))
    assert verify_clover_auth(headers, CODE)
    assert not verify_clover_auth(headers, OTHER_CODE)


def test_the_verifier_looks_the_header_up_case_insensitively() -> None:
    """A consumer's framework may have lower-cased the name on the way in."""
    assert verify_clover_auth({"x-clover-auth": CODE}, CODE)
    assert verify_clover_auth({"X-CLOVER-AUTH": CODE}, CODE)


def test_an_absent_header_never_verifies_even_against_an_empty_expectation() -> None:
    """Absence is not the empty string: a handler configured with no auth code
    yet must not accept an unauthenticated delivery."""
    assert not verify_clover_auth({"content-type": "application/json"}, CODE)
    assert not verify_clover_auth({}, "")


def test_a_non_string_header_value_is_a_failed_verification_not_a_stringified_one() -> None:
    """A framework that hands over `None` for a missing header must not be
    compared as the four-letter string "None" -- and must not raise either."""
    assert not verify_clover_auth({"X-Clover-Auth": None}, "None")
    assert not verify_clover_auth({"X-Clover-Auth": 12345}, "12345")
    assert not verify_clover_auth({"X-Clover-Auth": b"code"}, "code")


# ---------------------------------------------------------------------------
# The description, which is what an operator reads at /__unit/info.
# ---------------------------------------------------------------------------


def test_describe_names_the_header_the_absence_of_an_algorithm_and_the_source(
    signer: CloverWebhookSigner,
) -> None:
    described = signer.describe()
    assert described["header"] == "X-Clover-Auth"
    assert "no HMAC" in described["algorithm"]
    assert described["reference"] == "https://docs.clover.com/dev/docs/webhooks"
    assert described["verification"].startswith("JUDGMENT:")
    assert VERIFICATION_EVENT_TYPE in described["verification"]
