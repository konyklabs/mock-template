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

THE ONE EXCEPTION is the verification handshake, and it is JUDGMENT. The
documentation says the auth code is sent "in every message header after the
webhook callback URL is validated"; it does not say what the verification
POST itself carries. Reading "after" as "not before" is an inference, and
this unit sends the delivery carrying ``{"verificationCode": ...}`` with no
``X-Clover-Auth`` on that inference. The failure mode runs one way: a consumer
whose callback rejects unauthenticated POSTs passes the handshake here and may
find the real platform behaves differently -- either sending the code
unauthenticated too, or not. :meth:`CloverWebhookSigner.describe` publishes
the label so an operator reading ``/__unit/info`` sees it as a reading and
not a fact.

HOW THE SIGNER KNOWS. :meth:`CloverWebhookSigner.sign` recognises the unit's
own verification delivery by its type **and** its event id
(:func:`~vendorfake.clover.events.verification_event_id`). The type alone
would be forgeable: ``POST /__unit/webhooks/emit`` accepts any type string and
fans out to every ``*`` subscriber, so a caller could push an unauthenticated
delivery to a verified callback by naming the type. The emitter derives its
event ids from a digest and cannot produce ``verification:<id>``; only
:mod:`vendorfake.clover.surface.webhooks` does. An emitted event of that type
therefore carries the header like any other.

Where the secret lives: the core's subscription entity has one ``signature_key``
and the dispatcher hands it to the signer as ``SignInput.secret``. For Clover
that field *is* the auth code -- a seeded subscriber declares it, the
dashboard stand-in mints one from :class:`~vendorfake.clover.ids.CloverIds`.
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


def verify_clover_auth(headers: Mapping[str, object], expected: str) -> bool:
    """Whether a delivery's ``X-Clover-Auth`` is ``expected``.

    Mirrors ``verify_square_signature`` so a consumer can copy one helper per
    vendor into their fixtures. Header lookup is case-insensitive because a
    consumer's framework may have lower-cased the name, and the comparison is
    :func:`hmac.compare_digest` rather than ``==`` for the same reason the
    Square helper uses it: a consumer who copies ``==`` from here ships it.
    An absent header verifies as nothing, never as the empty string, and a
    value that is not a string -- a framework handing over ``None`` for a
    missing header, say -- is a failed verification rather than a comparison
    against ``"None"``.
    """
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
        verification POST (JUDGMENT; see the module docstring).

        Nothing else from the payload is read: not the body, not the URL, not
        the attempt. A retry carries the same header as the first send.
        """
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
