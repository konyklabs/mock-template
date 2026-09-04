"""The non-signature headers on a Lightspeed delivery.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/webhooks): a delivery is
``POST`` with ``Content-Type: application/x-www-form-urlencoded``, UTF-8, and
carries ``X-Signature``. That is the whole of it -- the page names no attempt
header, no event-type header and no retry header.

JUDGMENT -- **the other two headers are this fake's**, and are prefixed
``x-vendorfake-`` so a consumer cannot mistake either for Lightspeed's:

* ``x-vendorfake-attempt-number`` -- 1 on the first send, 2 on the first
  retry;
* ``x-vendorfake-retry-reason`` -- on a retry only, carrying the core's
  neutral outcome name.

The second exists because conformance C16 asks that a retry be
distinguishable from a first send by a header the vendor names, and the first
because an attempt counter that only appeared on retries would be the same
header twice. A handler must not depend on either: against the real API
neither is sent. The event type is NOT a header here, deliberately -- it
travels in the form body's ``payload`` where the vendor puts it, and inventing
a header for it would tempt a consumer to route on something Lightspeed never
sends.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.lightspeed.retry import ATTEMPT_NUMBER_HEADER, CONTENT_TYPE, RETRY_REASON_HEADER, RETRY_REASONS

__all__ = ["LightspeedDeliveryHeaders"]


class LightspeedDeliveryHeaders:
    """Satisfies ``DeliveryHeaderProvider``. Stateless."""

    __slots__ = ()

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        out = {
            "content-type": CONTENT_TYPE,
            ATTEMPT_NUMBER_HEADER: str(meta.attempt),
        }
        if meta.is_retry and meta.retry_reason is not None:
            out[RETRY_REASON_HEADER] = RETRY_REASONS[meta.retry_reason]
        return out
