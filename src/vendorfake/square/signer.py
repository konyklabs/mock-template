"""Square's webhook signature scheme: ``x-square-hmacsha256-signature``, per the algorithm Square
publishes.

INVARIANT: covers only the notification URL and the exact delivered bytes, not the attempt number -- a
redelivery carries the same signature.
DOCUMENTED: header name, HMAC-SHA-256, base64, over the signature key/URL/body. NOT DOCUMENTED: their
order. JUDGMENT, resolved from Square's own SDK source: ``notification_url + raw_body``, no separator.
https://developer.squareup.com/docs/webhooks/step3validate https://github.com/square/square-python-sdk/blob/master/src/square/utils/webhooks_helper.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping

from vendorfake.core.kernel.types import SignerProperties, SignInput
from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.square.delivery_headers import SquareDeliveryHeaders
from vendorfake.square.surface.common import SquareDeps

__all__ = [
    "SIGNATURE_HEADER",
    "SQUARE_SIGNER_PROPERTIES",
    "SquareWebhookSigner",
    "square_signature",
    "verify_square_signature",
]

SIGNATURE_HEADER = "x-square-hmacsha256-signature"

SQUARE_SIGNER_PROPERTIES = SignerProperties(
    url_bound=True,
    body_bound=True,
    secret_bound=True,
    signature_headers=(SIGNATURE_HEADER,),
)
"""What this scheme depends on: url, body and secret, not the attempt number."""

_SIGNATURE_DOC_URL = "https://developer.squareup.com/docs/webhooks/step3validate"


def square_signature(signature_key: str, notification_url: str, raw_body: bytes | str) -> str:
    """``base64(HMAC-SHA256(signature_key, notification_url + raw_body))``. Exported so callers and
    the fidelity test share one implementation."""
    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    payload = notification_url.encode("utf-8") + body
    digest = hmac.new(signature_key.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_square_signature(
    signature_key: str,
    notification_url: str,
    raw_body: bytes | str,
    signature: str,
) -> bool:
    """Whether ``signature`` matches, via :func:`hmac.compare_digest`, not ``==``."""
    return hmac.compare_digest(square_signature(signature_key, notification_url, raw_body), signature)


class SquareWebhookSigner:
    """Square's signing scheme plus delivery headers; holds the vendor since ``environment``
    resolves in ``hydrate``."""

    __slots__ = ("_deps", "_headers")

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps
        self._headers = SquareDeliveryHeaders(deps)

    @property
    def properties(self) -> SignerProperties:
        return SQUARE_SIGNER_PROPERTIES

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        """The signature header, over the exact bytes sent, never a re-serialisation."""
        return {
            SIGNATURE_HEADER: square_signature(
                payload.secret,
                payload.notification_url,
                payload.raw_body,
            )
        }

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Every non-signature delivery header. See :mod:`.delivery_headers`."""
        return self._headers.headers(meta)

    def describe(self) -> Mapping[str, str]:
        """What ``/__unit/info`` reports, incl. where the payload order came from."""
        return {
            "header": SIGNATURE_HEADER,
            "algorithm": "HMAC-SHA-256, base64",
            "payload": "notification_url + raw_body (no separator, UTF-8)",
            "reference": _SIGNATURE_DOC_URL,
            "payload_order_provenance": (
                "Square documents the three inputs but not their order; "
                "the order is taken from square-python-sdk and square-nodejs-sdk."
            ),
            "environment": self._deps.config.environment,
        }
