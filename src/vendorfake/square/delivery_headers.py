"""Square's own names for what the core knows neutrally about a delivery. The core emits no headers of its own;
every non-signature header is translated here or by :meth:`~vendorfake.square.signer.SquareWebhookSigner.sign`.
DOCUMENTED: retry headers appear only on a retry; delivery-timestamp and environment appear on every attempt.
https://developer.squareup.com/docs/webhooks/overview
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.square.retry import (
    CONTENT_TYPE,
    INITIAL_DELIVERY_HEADER,
    RETRY_NUMBER_HEADER,
    RETRY_REASON_HEADER,
    RETRY_REASONS,
)
from vendorfake.square.surface.common import SquareDeps

__all__ = ["ENVIRONMENT_HEADER", "SquareDeliveryHeaders"]

ENVIRONMENT_HEADER = "square-environment"
"""``Production`` or ``Sandbox``, read from the resolved vendor config on every attempt.
https://developer.squareup.com/docs/webhooks/build-with-webhooks"""


class SquareDeliveryHeaders:
    """Satisfies ``DeliveryHeaderProvider``. Holds the vendor, not its config, since the config resolves in
    ``hydrate`` after this object exists."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Every non-signature header for one attempt. A missing ``retry_reason`` on a retry emits no reason header
        rather than a guessed one: ``None`` means the core does not know why the prior attempt failed."""
        out = {
            "content-type": CONTENT_TYPE,
            ENVIRONMENT_HEADER: self._deps.config.environment,
            INITIAL_DELIVERY_HEADER: meta.initial_delivery_at,
        }
        if meta.is_retry:
            out[RETRY_NUMBER_HEADER] = str(meta.retry_number)
            if meta.retry_reason is not None:
                out[RETRY_REASON_HEADER] = RETRY_REASONS[meta.retry_reason]
        return out
