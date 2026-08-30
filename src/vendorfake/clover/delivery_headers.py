"""The non-signature headers on a Clover delivery -- and the honest problem
with them.

FOR: turning the neutral facts in
:class:`~vendorfake.core.webhooks.models.DeliveryMetadata` into the headers an
outbound delivery carries, so that the core -- which sends nothing of its own,
not even a content type -- puts on the wire only what this vendor names.

WHAT CLOVER DOCUMENTS: one header. ``X-Clover-Auth`` is the auth code, and it
is a *signature* header here (:mod:`vendorfake.clover.signer`), not a delivery
header. The body is JSON. That is the whole documented wire
(https://docs.clover.com/dev/docs/webhooks, fetched 2026-08-29) -- no retry
counter, no reason, no timestamp.

JUDGMENT -- **the three ``x-vendorfake-*`` headers below are this fake's,
not Clover's.** They exist because a redelivery is a real thing a consumer's
handler must survive, and the core's own contract (conformance C16: "retry
metadata appears only on a retry") makes that observable only if the vendor
names some retry-only header. Inventing ``x-clover-retry-number`` would teach a
consumer a header the real platform never sends, and the core's ``x-unit-``
namespace is reserved for *responses to a consumer* -- the same contract
refuses it on a delivery. So the headers carry the product's own name, which
is the one prefix a consumer cannot mistake for Clover's. A handler written
against this unit must not depend on them.

The rule for when they appear is the core's: the retry number and reason only
when :attr:`DeliveryMetadata.is_retry`, the initial-delivery timestamp on
every attempt with the same value each time.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.clover.retry import (
    CONTENT_TYPE,
    INITIAL_DELIVERY_HEADER,
    RETRY_NUMBER_HEADER,
    RETRY_REASON_HEADER,
    RETRY_REASONS,
)
from vendorfake.core.webhooks.models import DeliveryMetadata

__all__ = ["CloverDeliveryHeaders"]


class CloverDeliveryHeaders:
    """Satisfies ``DeliveryHeaderProvider``. Stateless: nothing in Clover's
    configuration reaches a delivery header."""

    __slots__ = ()

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        out = {
            "content-type": CONTENT_TYPE,
            INITIAL_DELIVERY_HEADER: meta.initial_delivery_at,
        }
        if meta.is_retry:
            out[RETRY_NUMBER_HEADER] = str(meta.retry_number)
            if meta.retry_reason is not None:
                out[RETRY_REASON_HEADER] = RETRY_REASONS[meta.retry_reason]
        return out
