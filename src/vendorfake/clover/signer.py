"""Clover's webhook authentication: one static header, no signature.

DOCUMENTED (https://docs.clover.com/dev/docs/webhooks): the auth code is sent
as ``X-Clover-Auth: <auth code>`` on every delivery, no HMAC/timestamp/nonce.
JUDGMENT: the verification POST is undocumented and sent with no auth
header; this unit recognises it by type + digest-derived event id. The
secret is the subscription's ``signature_key``, which for Clover *is* the
auth code.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping

from vendorfake.clover.delivery_headers import CloverDeliveryHeaders
from vendorfake.clover.events import VERIFICATION_EVENT_TYPE, verification_event_id
from vendorfake.core.kernel.types import SignerProperties, SignInput
from vendorfake.core.webhooks.models import DeliveryMetadata

__all__ = [
    "AUTH_HEADER",
    "CLOVER_SIGNER_PROPERTIES",
    "CloverWebhookSigner",
    "verify_clover_auth",
]

AUTH_HEADER = "X-Clover-Auth"
"""The documented header."""

_DOC_URL = "https://docs.clover.com/dev/docs/webhooks"

CLOVER_SIGNER_PROPERTIES = SignerProperties(
    url_bound=False,
    body_bound=False,
    secret_bound=True,
    signature_headers=(AUTH_HEADER.lower(),),
)
"""Bound to the secret and to nothing else."""


def verify_clover_auth(headers: Mapping[str, object], expected: str) -> bool:
    """Whether a delivery's ``X-Clover-Auth`` is ``expected``, checked
    case-insensitively with :func:`hmac.compare_digest`."""
    wanted = AUTH_HEADER.lower()
    for name, value in headers.items():
        if name.lower() == wanted:
            return isinstance(value, str) and hmac.compare_digest(value, expected)
    return False


class CloverWebhookSigner:
    """The static-header scheme plus its delivery headers. Satisfies ``Signer``."""

    __slots__ = ("_headers",)

    def __init__(self) -> None:
        self._headers = CloverDeliveryHeaders()

    @property
    def properties(self) -> SignerProperties:
        return CLOVER_SIGNER_PROPERTIES

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        """``X-Clover-Auth: <secret>``, or nothing for the unit's own
        verification POST (JUDGMENT)."""
        event = payload.event
        if event.type == VERIFICATION_EVENT_TYPE and event.event_id == verification_event_id(event.entity_id):
            return {}
        return {AUTH_HEADER: payload.secret}

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Every non-signature header. See :mod:`.delivery_headers`."""
        return self._headers.headers(meta)

    def describe(self) -> Mapping[str, str]:
        """What ``/__unit/info`` says about this scheme, provenance included."""
        return {
            "header": AUTH_HEADER,
            "algorithm": "static shared secret; no HMAC, no timestamp",
            "payload": "none -- the header is the subscription's auth code verbatim",
            "reference": _DOC_URL,
            "verification": (
                f"JUDGMENT: the unit's own {VERIFICATION_EVENT_TYPE!r} delivery carries no {AUTH_HEADER}. "
                "Clover documents the code as sent 'in every message header after the webhook callback "
                "URL is validated' and says nothing about the verification POST itself; a callback that "
                "rejects unauthenticated POSTs passes here and may behave differently against Clover."
            ),
        }
