"""Square's webhook signature, verified against an independent implementation.

The rule this file follows: **never verify the signer by calling the signer.**
:func:`independent_signature` below writes the documented algorithm out again
from :mod:`hmac`, :mod:`hashlib` and :mod:`base64`, so a passing assertion says
"the header matches the published scheme" rather than "the signer agrees with
itself". That is the only form of the assertion worth having, because a signer
that concatenated the body *before* the URL would be perfectly self-consistent
and would fail against every consumer's verification code.

    base64(HMAC-SHA256(signature_key, notification_url + raw_body))
    https://developer.squareup.com/docs/webhooks/step3validate
    https://github.com/square/square-python-sdk/blob/master/src/square/utils/webhooks_helper.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from vendorfake.core.kernel.types import PreparedEvent, SignInput
from vendorfake.square.config import SquareConfig
from vendorfake.square.signer import (
    SIGNATURE_HEADER,
    SquareWebhookSigner,
    square_signature,
    verify_square_signature,
)
from vendorfake.square.vendor import create_square_vendor

URL = "https://example.test/hooks"
OTHER_URL = "https://elsewhere.test/hooks"
KEY = "signature-key-one"
OTHER_KEY = "signature-key-two"
BODY = b'{"type":"order.created","event_id":"e1"}'
OTHER_BODY = b'{"type":"order.updated","event_id":"e1"}'


def independent_signature(signature_key: str, notification_url: str, raw_body: bytes) -> str:
    """The documented algorithm, written out from the standard library."""
    payload = notification_url.encode("utf-8") + raw_body
    return base64.b64encode(hmac.new(signature_key.encode("utf-8"), payload, hashlib.sha256).digest()).decode()


def sign_input(*, url: str = URL, secret: str = KEY, body: bytes = BODY, attempt: int = 1) -> SignInput:
    return SignInput(
        notification_url=url,
        raw_body=body,
        secret=secret,
        attempt=attempt,
        event=PreparedEvent(
            type="order.created", event_id="e1", entity_id="o1", created_at="2026-01-01T00:00:00.000Z", body={}
        ),
    )


@pytest.fixture
def signer() -> SquareWebhookSigner:
    """A signer over a vendor with the default (Sandbox) configuration."""
    vendor = create_square_vendor()
    signed = vendor.signer
    assert isinstance(signed, SquareWebhookSigner)
    return signed


# ---------------------------------------------------------------------------
# The algorithm.
# ---------------------------------------------------------------------------


def test_the_signature_is_the_documented_algorithm() -> None:
    """Header, algorithm and the three inputs are documented; the *order* of
    the inputs is not, and comes from Square's own SDKs. Both halves are pinned
    here so that "fixing" the order is a red test."""
    assert square_signature(KEY, URL, BODY) == independent_signature(KEY, URL, BODY)


def test_the_url_comes_first_and_the_body_second() -> None:
    """The one thing the documentation does not state. Reversing the two
    produces a perfectly valid HMAC that no consumer can verify, so the order
    is asserted directly rather than only through the round trip above."""
    reversed_order = base64.b64encode(hmac.new(KEY.encode(), BODY + URL.encode(), hashlib.sha256).digest()).decode()
    assert square_signature(KEY, URL, BODY) != reversed_order


def test_there_is_no_separator_between_the_url_and_the_body() -> None:
    """`notification_url + raw_body`, concatenated with nothing between them.

    A newline or a colon would be invisible in every other test in this file --
    both halves would still be present, in the right order -- and would break
    every consumer.
    """
    for separator in (b"\n", b":", b".", b" "):
        salted = base64.b64encode(
            hmac.new(KEY.encode(), URL.encode() + separator + BODY, hashlib.sha256).digest()
        ).decode()
        assert square_signature(KEY, URL, BODY) != salted


def test_a_string_body_signs_as_its_utf8_bytes() -> None:
    """The delivery path always passes bytes; the string overload exists for a
    consumer verifying a payload they already decoded, and must agree."""
    body = '{"merchant":"Café"}'
    assert square_signature(KEY, URL, body) == square_signature(KEY, URL, body.encode("utf-8"))


def test_the_verifier_accepts_the_signature_and_refuses_every_perturbation() -> None:
    signature = square_signature(KEY, URL, BODY)
    assert verify_square_signature(KEY, URL, BODY, signature)
    assert not verify_square_signature(OTHER_KEY, URL, BODY, signature)
    assert not verify_square_signature(KEY, OTHER_URL, BODY, signature)
    assert not verify_square_signature(KEY, URL, OTHER_BODY, signature)
    assert not verify_square_signature(KEY, URL, BODY, "not-a-signature")


# ---------------------------------------------------------------------------
# The declared properties, checked in the direction they are declared.
# ---------------------------------------------------------------------------


def test_the_signer_declares_all_three_bindings(signer: SquareWebhookSigner) -> None:
    assert signer.properties.url_bound is True
    assert signer.properties.body_bound is True
    assert signer.properties.secret_bound is True


def test_each_declared_binding_actually_holds(signer: SquareWebhookSigner) -> None:
    """One input varied at a time. Varying two at once would pass for a signer
    bound to neither -- which is the defect the conformance suite's four
    observations exist to separate."""
    base = signer.sign(sign_input())[SIGNATURE_HEADER]
    assert signer.sign(sign_input(url=OTHER_URL))[SIGNATURE_HEADER] != base
    assert signer.sign(sign_input(secret=OTHER_KEY))[SIGNATURE_HEADER] != base
    assert signer.sign(sign_input(body=OTHER_BODY))[SIGNATURE_HEADER] != base


def test_the_signature_is_not_bound_to_the_attempt(signer: SquareWebhookSigner) -> None:
    """A redelivery carries the same signature.

    `SignerProperties` has no `attempt_bound` field, so this is the assertion
    that stands in for one: a consumer who verified the first copy and
    deduplicated on `event_id` must be able to verify the twelfth. It is also
    what makes the conformance suite's forced-retry observation meaningful --
    same url, same secret, same body, different attempt, one signature.
    """
    first = signer.sign(sign_input(attempt=1))[SIGNATURE_HEADER]
    twelfth = signer.sign(sign_input(attempt=12))[SIGNATURE_HEADER]
    assert first == twelfth


def test_signing_is_deterministic(signer: SquareWebhookSigner) -> None:
    """No nonce, no salt, no clock: the same payload signs identically forever,
    which is what lets a delivery transcript be diffed between runs."""
    assert signer.sign(sign_input()) == signer.sign(sign_input())


def test_sign_returns_exactly_one_header(signer: SquareWebhookSigner) -> None:
    """Everything else a delivery carries comes from `headers()`. If a second
    key appeared here it would silently take precedence over the header
    provider, since the dispatcher merges the signature second."""
    assert list(signer.sign(sign_input())) == [SIGNATURE_HEADER]


# ---------------------------------------------------------------------------
# The description, which is what an operator reads at /__unit/info.
# ---------------------------------------------------------------------------


def test_describe_carries_the_provenance_of_the_concatenation_order(signer: SquareWebhookSigner) -> None:
    """The citation is part of the product, not a comment: the operator
    debugging a failed verification is exactly the person who needs to know the
    order came from Square's SDKs rather than from the docs page."""
    described = signer.describe()
    assert described["header"] == "x-square-hmacsha256-signature"
    assert described["algorithm"] == "HMAC-SHA-256, base64"
    assert described["payload"] == "notification_url + raw_body (no separator, UTF-8)"
    assert described["reference"] == "https://developer.squareup.com/docs/webhooks/step3validate"
    assert "sdk" in described["payload_order_provenance"].lower()


def test_the_described_environment_follows_the_resolved_config() -> None:
    """Read live rather than captured, because `hydrate` resolves the profile's
    `vendor` block after this object is built."""
    vendor = create_square_vendor(vendor_config={"environment": "Production"})
    signer = vendor.signer
    assert signer is not None
    assert signer.describe()["environment"] == "Production"
    assert SquareConfig().environment == "Sandbox"
