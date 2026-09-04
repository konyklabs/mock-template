"""Lightspeed's webhook signature: HMAC-SHA256 over the raw form body, hex.

FOR: producing the ``X-Signature`` header a consumer's verification code
checks, so that a handler that verifies correctly against this unit verifies
correctly against Lightspeed -- and one that does not fails here first.

WHAT LIGHTSPEED DOCUMENTS (https://x-series-api.lightspeedhq.com/docs/webhooks):
the header's exact format, quoted::

    X-Signature: signature=897hRT893qkA783M093ha903f,algorithm=HMAC-SHA256

Comma-separated ``key=value`` pairs in one header value. The algorithm is
named. The recipe is one sentence: "generate a signature by hashing the
webhook request body and compare it to the signature in the header for an
exact match".

JUDGMENT, and the loudest one in this package: **what "the webhook request
body" means.** The delivery is ``application/x-www-form-urlencoded`` with the
entity JSON inside a ``payload`` field, so the phrase has two readings -- the
raw form-encoded bytes as sent, or the ``payload`` field's JSON string after
decoding. The docs do not say, and a second, more targeted fetch confirmed the
ambiguity rather than resolving it. This unit signs the **raw form-encoded
body bytes**, which is the literal reading of "the request body", and
:meth:`LightspeedWebhookSigner.describe` says so at ``/__unit/info``. A
consumer verifying against the real API and finding the header disagrees
should try the ``payload``-only reading second;
:func:`lightspeed_signature_over_payload` is shipped so that reading is one
call away rather than a re-implementation.

The encoding is **hex**, also JUDGMENT: the page's own sample value
(``897hRT893qkA783M093ha903f``) is not hex and not valid base64 either -- it is
an illustration, not a spec -- so hex is chosen as the encoding a reader of
"hashing the body" reaches for first, and it is stated rather than assumed.

THE PROPERTIES FOLLOW: bound to the body and the secret, not to the URL and
not to the attempt. A retry re-sends the same bytes and the same header, which
is what lets a consumer deduplicating on the payload's own id verify a
redelivery.

THE SECRET. Lightspeed's own signature is computed with the application's
``client_secret``: there is no per-subscription secret in ``WebhookRequest``,
which carries only ``active``, ``type`` and ``url``. The core hands the signer
whatever the subscription's ``signature_key`` holds, so the seed and the
``POST /webhooks`` handler both set that key to the configured client secret --
see ``surface/webhooks.py``.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode

from vendorfake.core.kernel.types import PreparedEvent, SignerProperties, SignInput
from vendorfake.core.util.json import dump_json
from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.lightspeed.delivery_headers import LightspeedDeliveryHeaders

__all__ = [
    "ALGORITHM",
    "LIGHTSPEED_SIGNER_PROPERTIES",
    "SIGNATURE_HEADER",
    "LightspeedWebhookSigner",
    "lightspeed_signature",
    "lightspeed_signature_over_payload",
    "signature_header_value",
    "verify_lightspeed_signature",
]

SIGNATURE_HEADER = "X-Signature"
"""The documented header, in the documented casing."""

ALGORITHM = "HMAC-SHA256"
"""The ``algorithm=`` member's documented value."""

LIGHTSPEED_SIGNER_PROPERTIES = SignerProperties(
    url_bound=False,
    body_bound=True,
    secret_bound=True,
    signature_headers=(SIGNATURE_HEADER.lower(),),
)

_DOC_URL = "https://x-series-api.lightspeedhq.com/docs/webhooks"
_PAYLOAD_FIELD = "payload"


def _hmac_hex(secret: str, data: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()


def lightspeed_signature(secret: str, raw_body: bytes | str) -> str:
    """``hex(HMAC-SHA256(secret, raw_form_body))`` -- the reading this unit signs."""
    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    return _hmac_hex(secret, body)


def lightspeed_signature_over_payload(secret: str, raw_body: bytes | str) -> str:
    """The OTHER reading: the HMAC over the ``payload`` field's JSON string only.

    Shipped so a consumer chasing the ambiguity in the docs can try the second
    reading without writing their own form parser. Nothing in this unit sends
    it. A body with no ``payload`` field signs as the empty string, which is
    what the reading degenerates to.
    """
    text = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    fields = dict(parse_qsl(text, keep_blank_values=True))
    return _hmac_hex(secret, fields.get(_PAYLOAD_FIELD, "").encode("utf-8"))


def signature_header_value(signature: str) -> str:
    """``signature=<hex>,algorithm=HMAC-SHA256`` -- the documented header format."""
    return f"signature={signature},algorithm={ALGORITHM}"


def verify_lightspeed_signature(secret: str, raw_body: bytes | str, header_value: str) -> bool:
    """Whether ``header_value`` is what this scheme produces for ``raw_body``.

    Shipped for consumers to copy. It parses the comma-separated members the
    way the header is documented, checks the algorithm, and compares with
    :func:`hmac.compare_digest`, never ``==``.
    """
    members: dict[str, str] = {}
    for part in header_value.split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            members[key] = value
    if members.get("algorithm") != ALGORITHM:
        return False
    return hmac.compare_digest(lightspeed_signature(secret, raw_body), members.get("signature", ""))


class LightspeedWebhookSigner:
    """The HMAC scheme plus the delivery headers. Satisfies ``Signer``."""

    __slots__ = ("_headers",)

    def __init__(self) -> None:
        self._headers = LightspeedDeliveryHeaders()

    @property
    def properties(self) -> SignerProperties:
        return LIGHTSPEED_SIGNER_PROPERTIES

    def encode_body(self, event: PreparedEvent) -> bytes:
        """The delivery body: ``application/x-www-form-urlencoded``.

        Satisfies the core's :class:`~vendorfake.core.webhooks.models.BodyEncodingSigner`,
        which is the hook that lets a vendor whose delivery is not JSON send
        what it documents rather than a body its own content-type header
        contradicts. ``event.body`` is the *fields* mapping the event mapper
        built; each value already a string is sent as it is, and every other
        value is JSON-encoded -- which is what makes ``payload`` "a
        JSON-encoded object with entity details" without the mapper having to
        serialise it itself.

        A body that is not a mapping -- the control plane's
        ``POST /__unit/webhooks/emit`` accepts any document -- becomes the
        whole ``payload``, which is the field the docs make required.
        """
        body = event.body
        fields = dict(body) if isinstance(body, Mapping) else {_PAYLOAD_FIELD: body}
        pairs = [
            (name, value if isinstance(value, str) else dump_json(value).decode("utf-8"))
            for name, value in fields.items()
        ]
        return urlencode(pairs).encode("utf-8")

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        """The one signature header, over the exact bytes about to be sent."""
        return {SIGNATURE_HEADER: signature_header_value(lightspeed_signature(payload.secret, payload.raw_body))}

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        return self._headers.headers(meta)

    def describe(self) -> Mapping[str, str]:
        return {
            "header": SIGNATURE_HEADER,
            "format": "signature=<hex>,algorithm=HMAC-SHA256 (comma-separated key=value pairs, one header)",
            "algorithm": "HMAC-SHA-256, lowercase hex",
            "payload": "the raw application/x-www-form-urlencoded request body, exactly as sent",
            "secret": "the application's client_secret; Lightspeed's WebhookRequest carries no per-hook secret",
            "reference": _DOC_URL,
            "payload_provenance": (
                "JUDGMENT: the docs say only 'hashing the webhook request body' and the body is form-encoded "
                "with the entity JSON inside a payload field, so 'the body' has two readings. This unit signs "
                "the raw form bytes -- the literal reading. The other is "
                "vendorfake.lightspeed.signer.lightspeed_signature_over_payload."
            ),
            "encoding_provenance": (
                "JUDGMENT: the page's sample signature value is neither hex nor base64, so it settles nothing; "
                "hex is this project's choice and is stated rather than assumed."
            ),
        }
