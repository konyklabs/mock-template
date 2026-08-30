"""Toast's webhook signature: HMAC-SHA256 over the body and the timestamp.

FOR: producing the ``Toast-Signature`` header a consumer's verification code
checks, with the algorithm Toast publishes, so that a handler that verifies
correctly against this unit verifies correctly against Toast -- and one that
does not fails here first.

WHAT TOAST DOCUMENTS (https://doc.toasttab.com/doc/devguide/apiMessageSigning.html):
the signature is "derived by concatenating the body and timestamp of the
webhook message into a string, which is hashed and then signed using the
HMAC-SHA256 algorithm and the secret key"; the Java sample is
``signature = body + timestamp`` -> ``HmacSHA256(secret)`` -> Base64 ->
``Toast-Signature``; the secret is generated per subscription per environment.

JUDGMENT, and the loudest one in this package (audit gap 3): **which
timestamp string is appended.** The page never says. There is no timestamp
header on a delivery (apiHttpHeaders.html lists none), so the envelope's own
``timestamp`` field is the only candidate, and that is what this unit appends:
``body_bytes + body["timestamp"]``, the timestamp exactly as it is spelled
inside the body. A consumer verifying against Toast and finding the header
disagrees should try this reading first and the raw body alone second; the
``describe()`` block at ``/__unit/info`` says the same. When the delivered
body is not an object with a string ``timestamp`` -- the control plane's
``POST /__unit/webhooks/emit`` accepts any document -- nothing is appended.

THE PROPERTIES FOLLOW: bound to the body and the secret, not to the URL and
not to the attempt (a retry re-sends the same bytes and the same header;
"updates ... more than once" is the documented at-least-once, and a consumer
deduplicating on ``guid`` must find the redelivery verifiable).
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
    """``body + timestamp`` -- the bytes the HMAC covers. See the module
    docstring for what "timestamp" is read as."""
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
    """Whether ``signature`` is what this scheme produces for ``raw_body``.

    Shipped for consumers to copy; the comparison is
    :func:`hmac.compare_digest`, never ``==``.
    """
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
