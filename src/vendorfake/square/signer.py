"""Square's webhook signature scheme, and the two directions it is bound in.

FOR: producing the ``x-square-hmacsha256-signature`` header a consumer's own
verification code will check, using the algorithm Square publishes, so that a
consumer who copies Square's documented verification snippet into their handler
finds it passes against this unit and would pass against Square.

INVARIANT: **the signature covers the notification URL and the exact delivered
bytes, and nothing else.** In particular it is *not* bound to the attempt
number: the same event redelivered after a failure carries the same signature,
because a consumer deduplicating on ``event_id`` and verifying once must not
find the second copy unverifiable. :data:`SQUARE_SIGNER_PROPERTIES` declares
the three directions that do hold, and the conformance suite checks each of
them in the direction declared rather than assuming the first vendor's answer.

WHAT SQUARE DOCUMENTS, AND WHAT IT DOES NOT
-------------------------------------------
Documented at https://developer.squareup.com/docs/webhooks/step3validate
(fetched 2026-08-25): the header name ``x-square-hmacsha256-signature``; the
algorithm "HMAC-SHA-256"; and that the signature is "generated using: The
signature key for your webhook subscription. The notification URL for your
webhook subscription. The raw body of the request." Base64 encoding is evident
from the documented example value ``2kRE5qRU2tR+tBGlDwMEw2avJ7QM4ikPYD/PJ3bd9Og=``.

NOT DOCUMENTED: the order in which those three inputs are combined. Every code
sample on that page delegates to an SDK helper. Square's own SDKs are the
authority here, because they are what a consumer's verification code actually
runs, and both of them concatenate the notification URL first and the raw body
second with no separator::

    payload = notification_url + request_body

    https://github.com/square/square-python-sdk/blob/master/src/square/utils/webhooks_helper.py
    https://github.com/square/square-nodejs-sdk/blob/master/src/wrapper/WebhooksHelper.ts

**Keep that citation.** Without it the order looks arbitrary and the next
maintainer "fixes" it, at which point every consumer's verification silently
starts failing against a fake that still looks correct. JUDGMENT, resolved from
public SDK source rather than from the documentation page, and labelled as such.

``square-environment`` and the retry headers live in
:mod:`vendorfake.square.delivery_headers`; this module signs. Both arrive on
the wire through :meth:`SquareWebhookSigner.headers` and
:meth:`SquareWebhookSigner.sign`, which are one protocol precisely so that a
vendor cannot register a signature and forget its delivery headers.
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
"""The documented header name. Spelled once, here."""

SQUARE_SIGNER_PROPERTIES = SignerProperties(
    url_bound=True,
    body_bound=True,
    secret_bound=True,
    signature_headers=(SIGNATURE_HEADER,),
)
"""What this scheme actually depends on, declared for the conformance suite.

All three are true and each is separately observable: change the subscriber's
notification URL, change its signature key, or change one byte of the body, and
the header moves. Nothing else moves it -- the attempt number in particular,
which is why a redelivery verifies with the same code path as the first send.
"""

_SIGNATURE_DOC_URL = "https://developer.squareup.com/docs/webhooks/step3validate"


def square_signature(signature_key: str, notification_url: str, raw_body: bytes | str) -> str:
    """``base64(HMAC-SHA256(signature_key, notification_url + raw_body))``.

    Exported rather than kept private because the README example, a consumer's
    own fixture and this package's tests should all be able to reach *one*
    implementation -- with the deliberate exception of the fidelity test for
    this function, which writes the algorithm out again from
    :mod:`hmac` and :mod:`hashlib` so that the assertion is not "the signer
    agrees with itself".

    ``raw_body`` is bytes on the delivery path and is accepted as ``str`` only
    for a caller verifying a payload they already decoded; it is encoded as
    UTF-8, which is what the delivery path produced in the first place.
    """
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
    """Whether ``signature`` is the one this scheme produces for that payload.

    Shipped alongside the signer because consumers copy a fake's helper into
    their own handler, and the comparison is
    :func:`hmac.compare_digest` -- as Square's own Python SDK does it -- rather
    than ``==``. The timing difference is irrelevant against a fake and the
    habit is not: a consumer who copies ``==`` from here ships it.
    """
    return hmac.compare_digest(square_signature(signature_key, notification_url, raw_body), signature)


class SquareWebhookSigner:
    """Square's signing scheme plus its delivery headers. Satisfies ``Signer``.

    Holds the vendor rather than a copy of its configuration, for the reason
    every surface does: ``environment`` is resolved from the profile in
    ``hydrate``, which runs after this object is built, and a signer that had
    captured it would stamp ``square-environment: Sandbox`` on a unit an
    operator configured as Production.
    """

    __slots__ = ("_deps", "_headers")

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps
        self._headers = SquareDeliveryHeaders(deps)

    @property
    def properties(self) -> SignerProperties:
        return SQUARE_SIGNER_PROPERTIES

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        """The one signature header, over the URL and the exact bytes sent.

        ``payload.raw_body`` is what the dispatcher is about to hand the sink,
        never a re-serialisation of ``payload.event.body``: re-encoding between
        signing and sending is the classic way a signature scheme becomes
        unverifiable over key order or whitespace, and the core's
        :class:`~vendorfake.core.kernel.types.SignInput` carries the bytes for
        exactly that reason.
        """
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
        """What ``/__unit/info`` says about this scheme.

        The provenance note is part of the description and not a comment: an
        operator reading ``/__unit/info`` to work out why their verification
        fails is exactly the person who needs to know that the concatenation
        order came from Square's SDKs rather than from the docs page.
        """
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
