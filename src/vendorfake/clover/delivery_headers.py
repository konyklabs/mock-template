"""Turns :class:`~vendorfake.core.webhooks.models.DeliveryMetadata` into the
non-signature headers of a Clover webhook delivery; the core adds none.

DOCUMENTED: Clover's only delivery header is the signature header
``X-Clover-Auth`` (:mod:`vendorfake.clover.signer`); the body is JSON
(https://docs.clover.com/dev/docs/webhooks). JUDGMENT: the three
``x-vendorfake-*`` headers are this fake's own, appearing only on a retry.
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
    """Satisfies ``DeliveryHeaderProvider``. Stateless."""

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
