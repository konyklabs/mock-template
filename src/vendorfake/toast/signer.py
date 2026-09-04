"""Toast's webhook signature: HMAC-SHA256 over the body and the timestamp.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiMessageSigning.html):
``signature = body + timestamp`` -> ``HmacSHA256(secret)`` -> Base64 ->
``Toast-Signature``; the secret is generated per subscription per environment.

JUDGMENT (audit gap 3): which timestamp string is appended -- the page never
says, and no timestamp header is documented on a delivery, so this unit
appends the envelope's own ``timestamp`` field verbatim; ``describe()`` at
``/__unit/info`` states this reading.

Bound to the body and the secret, not the URL or the attempt, so a retry
resends the same signature.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping

from vendorfake.core.kernel.types import SignerProperties, SignInput
from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.toast.delivery_headers import ToastDeliveryHeaders

__all__ = [
    "SIGNATURE_HEADER",
    "TOAST_SIGNER_PROPERTIES",
    "ToastWebhookSigner",
    "signed_payload",
    "toast_signature",
    "verify_toast_signature",
]

SIGNATURE_HEADER = "Toast-Signature"
"""The documented header, in the documented casing."""

TOAST_SIGNER_PROPERTIES = SignerProperties(
    url_bound=False,
    body_bound=True,
    secret_bound=True,
    signature_headers=(SIGNATURE_HEADER.lower(),),
)

_DOC_URL = "https://doc.toasttab.com/doc/devguide/apiMessageSigning.html"


def signed_payload(raw_body: bytes | str) -> bytes:
    """``body + timestamp`` -- the bytes the HMAC covers; see the module docstring."""
    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    timestamp = ""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("timestamp"), str):
        timestamp = parsed["timestamp"]
    return body + timestamp.encode("utf-8")


def toast_signature(secret: str, raw_body: bytes | str) -> str:
    """``Base64(HMAC-SHA256(secret, body + timestamp))``."""
    digest = hmac.new(secret.encode("utf-8"), signed_payload(raw_body), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_toast_signature(secret: str, raw_body: bytes | str, signature: str) -> bool:
    """Whether ``signature`` is what this scheme produces for ``raw_body``,
    compared with :func:`hmac.compare_digest`, never ``==``."""
    return hmac.compare_digest(toast_signature(secret, raw_body), signature)


class ToastWebhookSigner:
    """The HMAC scheme plus the delivery headers. Satisfies ``Signer``."""

    __slots__ = ("_headers",)

    def __init__(self) -> None:
        self._headers = ToastDeliveryHeaders()

    @property
    def properties(self) -> SignerProperties:
        return TOAST_SIGNER_PROPERTIES

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        """The one signature header, over the exact bytes about to be sent."""
        return {SIGNATURE_HEADER: toast_signature(payload.secret, payload.raw_body)}

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        return self._headers.headers(meta)

    def describe(self) -> Mapping[str, str]:
        return {
            "header": SIGNATURE_HEADER,
            "algorithm": "HMAC-SHA-256, base64",
            "payload": "raw_body + body.timestamp (the envelope's own timestamp string, no separator, UTF-8)",
            "reference": _DOC_URL,
            "timestamp_provenance": (
                "JUDGMENT: Toast documents 'the body and timestamp of the webhook message' and no timestamp "
                "header; the envelope's timestamp field is the only candidate and is what this unit appends."
            ),
        }
