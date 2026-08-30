"""Toast-Signature: HMAC-SHA256 over body + timestamp, written out independently.

https://doc.toasttab.com/doc/devguide/apiMessageSigning.html
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from vendorfake.core.kernel.types import PreparedEvent, SignInput
from vendorfake.core.util.json import dump_json
from vendorfake.toast.signer import (
    SIGNATURE_HEADER,
    TOAST_SIGNER_PROPERTIES,
    ToastWebhookSigner,
    signed_payload,
    toast_signature,
    verify_toast_signature,
)
from vendorfake.toast.vendor import create_toast_vendor

SECRET = "unit-seeded-toast-webhook-secret"
OTHER_SECRET = "another-secret"
ENVELOPE = {
    "timestamp": "2024-03-28T15:11:01.050Z",
    "eventCategory": "stock",
    "eventType": "in_stock",
    "guid": "e1",
    "details": {"restaurantGuid": "r1"},
}
BODY = dump_json(ENVELOPE)


def sign_input(
    *, url: str = "https://example.test/hooks", secret: str = SECRET, body: bytes = BODY, attempt: int = 1
) -> SignInput:
    return SignInput(
        notification_url=url,
        raw_body=body,
        secret=secret,
        attempt=attempt,
        event=PreparedEvent(
            type="in_stock", event_id="e1", entity_id="i1", created_at=ENVELOPE["timestamp"], body=ENVELOPE
        ),
    )


@pytest.fixture
def signer() -> ToastWebhookSigner:
    signed = create_toast_vendor().signer
    assert isinstance(signed, ToastWebhookSigner)
    return signed


def test_the_java_sample_written_out_again_body_plus_timestamp_hmac_base64(signer: ToastWebhookSigner) -> None:
    """The independent check: never "the signer agrees with itself"."""
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), BODY + ENVELOPE["timestamp"].encode(), hashlib.sha256).digest()
    ).decode("ascii")
    assert signer.sign(sign_input()) == {"Toast-Signature": expected}
    assert toast_signature(SECRET, BODY) == expected
    assert SIGNATURE_HEADER == "Toast-Signature"
    assert signed_payload(BODY) == BODY + b"2024-03-28T15:11:01.050Z"


def test_the_appended_timestamp_is_the_envelopes_own_and_nothing_when_there_is_none() -> None:
    """JUDGMENT (gap 3): the body's timestamp field is the only candidate; a
    body that is not a Toast envelope gets the raw body alone."""
    assert signed_payload(b'{"probe":"one"}') == b'{"probe":"one"}'
    assert signed_payload(b"not json") == b"not json"
    assert signed_payload(b'{"timestamp": 5}') == b'{"timestamp": 5}'


def test_the_signer_declares_body_and_secret_bound_and_not_url_bound(signer: ToastWebhookSigner) -> None:
    assert signer.properties is TOAST_SIGNER_PROPERTIES
    assert signer.properties.body_bound and signer.properties.secret_bound and not signer.properties.url_bound
    assert signer.properties.signature_headers == ("toast-signature",)


def test_each_declared_direction_actually_holds(signer: ToastWebhookSigner) -> None:
    base = signer.sign(sign_input())[SIGNATURE_HEADER]
    assert signer.sign(sign_input(secret=OTHER_SECRET))[SIGNATURE_HEADER] != base
    assert signer.sign(sign_input(body=dump_json({**ENVELOPE, "guid": "e2"})))[SIGNATURE_HEADER] != base
    assert signer.sign(sign_input(url="https://elsewhere.test/hooks"))[SIGNATURE_HEADER] == base
    assert signer.sign(sign_input(attempt=3))[SIGNATURE_HEADER] == base
    assert signer.sign(sign_input()) == signer.sign(sign_input())


def test_the_verifier_round_trips_and_uses_a_constant_time_compare() -> None:
    signature = toast_signature(SECRET, BODY)
    assert verify_toast_signature(SECRET, BODY, signature)
    assert verify_toast_signature(SECRET, BODY.decode("utf-8"), signature)
    assert not verify_toast_signature(OTHER_SECRET, BODY, signature)
    assert not verify_toast_signature(SECRET, BODY + b" ", signature)


def test_describe_names_the_header_the_payload_and_the_judgment(signer: ToastWebhookSigner) -> None:
    described = signer.describe()
    assert described["header"] == "Toast-Signature"
    assert "HMAC-SHA-256" in described["algorithm"]
    assert "body.timestamp" in described["payload"]
    assert described["timestamp_provenance"].startswith("JUDGMENT")
    assert described["reference"].endswith("apiMessageSigning.html")
