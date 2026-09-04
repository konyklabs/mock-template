"""The non-signature headers on a Lightspeed delivery.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/webhooks): only
``Content-Type: application/x-www-form-urlencoded`` and ``X-Signature``.
JUDGMENT: the two ``x-vendorfake-`` headers are this fake's own --
``attempt-number`` for conformance C16, ``retry-reason`` on retries only.
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
