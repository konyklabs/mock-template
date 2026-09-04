"""Lightspeed's webhook signature: HMAC-SHA256 over the raw form body, hex.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/webhooks): header format is
``X-Signature: signature=<hex>,algorithm=HMAC-SHA256``; recipe is "hash the webhook
request body and compare it to the signature in the header".

JUDGMENT: "the webhook request body" reads as the raw form bytes here, not the decoded
``payload`` field's JSON; the hex encoding is also JUDGMENT -- see
:meth:`LightspeedWebhookSigner.describe` for both readings and why.

Bound to the body and the secret, not the URL; signs with the application's
``client_secret`` since ``WebhookRequest`` carries none of its own.
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
    """The other reading: HMAC over just the ``payload`` field's JSON string; nothing in this unit sends it."""
    text = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    fields = dict(parse_qsl(text, keep_blank_values=True))
    return _hmac_hex(secret, fields.get(_PAYLOAD_FIELD, "").encode("utf-8"))


def signature_header_value(signature: str) -> str:
    """``signature=<hex>,algorithm=HMAC-SHA256`` -- the documented header format."""
    return f"signature={signature},algorithm={ALGORITHM}"


def verify_lightspeed_signature(secret: str, raw_body: bytes | str, header_value: str) -> bool:
    """Whether ``header_value`` is what this scheme produces for ``raw_body``; compares
    with :func:`hmac.compare_digest`, never ``==``."""
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

        Satisfies :class:`~vendorfake.core.webhooks.models.BodyEncodingSigner`. String
        values pass through as-is; everything else is JSON-encoded. A non-mapping body
        (e.g. from ``POST /__unit/webhooks/emit``) becomes the whole ``payload`` field.
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
