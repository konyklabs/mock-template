"""The ``X-Signature`` scheme, the documented header format, and the ambiguity.

The recipe is genuinely ambiguous in the vendor's own documentation -- "hashing
the webhook request body" over a form-encoded body with JSON inside a field --
so these tests pin which reading this unit takes AND that the other reading is
reachable, because a consumer chasing a mismatch against the real API needs it.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode

from vendorfake.core.kernel.types import PreparedEvent, SignerProperties, SignInput
from vendorfake.core.util.json import dump_json
from vendorfake.lightspeed.signer import (
    ALGORITHM,
    LIGHTSPEED_SIGNER_PROPERTIES,
    SIGNATURE_HEADER,
    LightspeedWebhookSigner,
    lightspeed_signature,
    lightspeed_signature_over_payload,
    signature_header_value,
    verify_lightspeed_signature,
)

SECRET = "unit-lightspeed-client-secret"
#: The exact bytes a delivery carries: the payload JSON is encoded with the
#: core's own wire encoder (compact separators), because a signature covers
#: the bytes that were sent and `json.dumps`' default spacing would not be them.
PAYLOAD_JSON = dump_json({"a": 1}).decode("utf-8")
BODY = urlencode([("payload", PAYLOAD_JSON), ("domain_prefix", "x"), ("environment", "production")])


def _event(body: object) -> PreparedEvent:
    return PreparedEvent(
        type="register_closure.create",
        event_id="evt_1",
        entity_id="ent_1",
        created_at="2026-09-04T12:00:00.000Z",
        body=body,
    )


def test_the_header_format_is_the_documented_one() -> None:
    """``X-Signature: signature=<value>,algorithm=HMAC-SHA256`` -- comma
    separated key=value pairs in one header value."""
    assert signature_header_value("abc") == "signature=abc,algorithm=HMAC-SHA256"
    assert SIGNATURE_HEADER == "X-Signature"
    assert ALGORITHM == "HMAC-SHA256"


def test_the_signature_is_hex_hmac_sha256_over_the_raw_body() -> None:
    expected = hmac.new(SECRET.encode(), BODY.encode(), hashlib.sha256).hexdigest()
    assert lightspeed_signature(SECRET, BODY) == expected
    assert len(expected) == 64


def test_bytes_and_text_sign_identically() -> None:
    assert lightspeed_signature(SECRET, BODY) == lightspeed_signature(SECRET, BODY.encode("utf-8"))


def test_the_other_reading_is_shipped_and_differs() -> None:
    """The docs do not settle whether "the body" is the raw form bytes or the
    ``payload`` field's JSON string. This unit signs the former; the latter is
    one call away so a consumer can try it without writing a form parser."""
    assert lightspeed_signature_over_payload(SECRET, BODY) != lightspeed_signature(SECRET, BODY)
    expected = hmac.new(SECRET.encode(), PAYLOAD_JSON.encode(), hashlib.sha256).hexdigest()
    assert lightspeed_signature_over_payload(SECRET, BODY) == expected


def test_verify_accepts_what_the_scheme_produces() -> None:
    assert verify_lightspeed_signature(SECRET, BODY, signature_header_value(lightspeed_signature(SECRET, BODY)))


def test_verify_rejects_a_wrong_secret_a_changed_body_and_a_wrong_algorithm() -> None:
    header = signature_header_value(lightspeed_signature(SECRET, BODY))
    assert not verify_lightspeed_signature("other", BODY, header)
    assert not verify_lightspeed_signature(SECRET, BODY + "&x=1", header)
    assert not verify_lightspeed_signature(SECRET, BODY, header.replace(ALGORITHM, "HMAC-SHA1"))
    assert not verify_lightspeed_signature(SECRET, BODY, "not-a-header")


def test_the_declared_properties_match_the_scheme() -> None:
    """Conformance checks the directions a signer actually claims, so a claim
    the scheme does not honour is worse than none."""
    assert (
        SignerProperties(url_bound=False, body_bound=True, secret_bound=True, signature_headers=("x-signature",))
        == LIGHTSPEED_SIGNER_PROPERTIES
    )


def test_the_signature_does_not_move_with_the_url_or_the_attempt() -> None:
    """A retry re-sends the same bytes and the same header, which is what lets
    a consumer deduplicating on the payload verify a redelivery."""
    signer = LightspeedWebhookSigner()
    first = signer.sign(
        SignInput(
            notification_url="https://a.example/h", raw_body=BODY.encode(), secret=SECRET, attempt=1, event=_event({})
        )
    )
    second = signer.sign(
        SignInput(
            notification_url="https://b.example/h", raw_body=BODY.encode(), secret=SECRET, attempt=7, event=_event({})
        )
    )
    assert first == second


def test_encode_body_form_encodes_the_event_fields() -> None:
    """The core's default is JSON; this vendor declares
    ``BodyEncodingSigner.encode_body`` so its content-type header cannot
    contradict its body."""
    signer = LightspeedWebhookSigner()
    encoded = signer.encode_body(
        _event({"payload": {"a": 1}, "domain_prefix": "x", "environment": "production"})
    ).decode("utf-8")
    assert encoded == BODY


def test_encode_body_puts_a_non_mapping_document_under_payload() -> None:
    """The control plane's emitter accepts any document, and ``payload`` is the
    field the docs make required."""
    encoded = LightspeedWebhookSigner().encode_body(_event([1, 2])).decode("utf-8")
    assert encoded == urlencode([("payload", "[1,2]")])


def test_describe_states_both_judgment_calls() -> None:
    described = LightspeedWebhookSigner().describe()
    assert "JUDGMENT" in described["payload_provenance"]
    assert "JUDGMENT" in described["encoding_provenance"]
    assert described["reference"].startswith("https://x-series-api.lightspeedhq.com/")
