"""Clover's webhook authentication: one static header, no signature.

FOR: putting ``X-Clover-Auth`` on every delivery the way Clover does, so that a
consumer's handler -- which compares that header against the auth code from
the dashboard and nothing else -- passes against this unit exactly as it would
against Clover.

WHAT CLOVER DOCUMENTS (https://docs.clover.com/dev/docs/webhooks, fetched
2026-08-29): after the callback URL is validated, Clover sends "the auth code
in every message header after the webhook callback URL is validated", as
``X-Clover-Auth: <auth code>``. That is the whole scheme. There is no HMAC, no
timestamp, no nonce, and nothing about the body or the URL enters into it.

THE PROPERTIES FOLLOW FROM THAT and are declared, not assumed:
:data:`CLOVER_SIGNER_PROPERTIES` says the header is bound to the subscription
secret and to **nothing else** -- two subscribers with different callback URLs
and the same auth code receive the same header, and the same subscriber
receives the same header for every body. The conformance suite reads this
declaration and checks each direction *as declared*, which is what makes a
static scheme conformant rather than merely tolerated (``checks/webhooks.py``,
C09).

THE ONE EXCEPTION is the verification handshake. The auth code is documented
as sent only *after* validation, and the verification POST is what validates,
so the delivery carrying ``{"verificationCode": ...}`` goes out with no
``X-Clover-Auth``. :meth:`CloverWebhookSigner.sign` recognises that event by
its type; the surface that sends it is :mod:`vendorfake.clover.surface.webhooks`.

Where the secret lives: the core's subscription entity has one ``signature_key``
and the dispatcher hands it to the signer as ``SignInput.secret``. For Clover
that field *is* the auth code -- a seeded subscriber declares it, the
dashboard stand-in mints one from :class:`~vendorfake.clover.ids.CloverIds`.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping

from vendorfake.clover.delivery_headers import CloverDeliveryHeaders
from vendorfake.clover.events import VERIFICATION_EVENT_TYPE
from vendorfake.core.kernel.types import SignerProperties, SignInput
from vendorfake.core.webhooks.models import DeliveryMetadata

__all__ = [
    "AUTH_HEADER",
    "CLOVER_SIGNER_PROPERTIES",
    "CloverWebhookSigner",
    "verify_clover_auth",
]

AUTH_HEADER = "X-Clover-Auth"
"""The documented header, in the documented spelling. HTTP header names are
case-insensitive on the wire and :func:`verify_clover_auth` looks it up that
way; the delivery log keeps this casing."""

_DOC_URL = "https://docs.clover.com/dev/docs/webhooks"

CLOVER_SIGNER_PROPERTIES = SignerProperties(
    url_bound=False,
    body_bound=False,
    secret_bound=True,
    signature_headers=(AUTH_HEADER.lower(),),
)
"""Bound to the secret and to nothing else. See the module docstring."""


def verify_clover_auth(headers: Mapping[str, str], expected: str) -> bool:
    """Whether a delivery's ``X-Clover-Auth`` is ``expected``.

    Mirrors ``verify_square_signature`` so a consumer can copy one helper per
    vendor into their fixtures. Header lookup is case-insensitive because a
    consumer's framework may have lower-cased the name, and the comparison is
    :func:`hmac.compare_digest` rather than ``==`` for the same reason the
    Square helper uses it: a consumer who copies ``==`` from here ships it.
    An absent header verifies as nothing, never as the empty string.
    """
    wanted = AUTH_HEADER.lower()
    for name, value in headers.items():
        if name.lower() == wanted:
            return hmac.compare_digest(str(value), expected)
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
        """``X-Clover-Auth: <secret>``, or nothing for the verification POST.

        Nothing else from the payload is read: not the body, not the URL, not
        the attempt. A retry carries the same header as the first send.
        """
        if payload.event.type == VERIFICATION_EVENT_TYPE:
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
                f"the {VERIFICATION_EVENT_TYPE!r} delivery carries no {AUTH_HEADER}: the code is "
                "documented as sent only after the callback URL is validated"
            ),
        }
