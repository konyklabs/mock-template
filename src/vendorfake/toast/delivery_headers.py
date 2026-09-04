"""The non-signature headers on a Toast delivery.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiHttpHeaders.html):
``Toast-Attempt-Number``, ``Toast-Event-Type``, ``Toast-Event-Category``,
``Toast-Restaurant-External-ID`` (omitted if not restaurant-scoped) and
``Content-Type``, all read from the delivered body so headers and body never
disagree.

JUDGMENT: ``x-vendorfake-retry-reason`` is this fake's own retry-only header,
undocumented by Toast, needed by conformance check C16.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.toast.retry import ATTEMPT_NUMBER_HEADER, CONTENT_TYPE, RETRY_REASON_HEADER, RETRY_REASONS

__all__ = ["EVENT_CATEGORY_HEADER", "EVENT_TYPE_HEADER", "RESTAURANT_ID_HEADER", "ToastDeliveryHeaders"]

EVENT_TYPE_HEADER = "Toast-Event-Type"
EVENT_CATEGORY_HEADER = "Toast-Event-Category"
RESTAURANT_ID_HEADER = "Toast-Restaurant-External-ID"


class ToastDeliveryHeaders:
    """Satisfies ``DeliveryHeaderProvider``. Stateless."""

    __slots__ = ()

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        body: Any = meta.event.body
        envelope = body if isinstance(body, Mapping) else {}
        event_type = envelope.get("eventType")
        category = envelope.get("eventCategory")
        details = envelope.get("details")
        restaurant = details.get("restaurantGuid") if isinstance(details, Mapping) else None
        out = {
            "content-type": CONTENT_TYPE,
            ATTEMPT_NUMBER_HEADER: str(meta.attempt),
            EVENT_TYPE_HEADER: event_type if isinstance(event_type, str) else meta.event.type,
            EVENT_CATEGORY_HEADER: category if isinstance(category, str) else meta.event.type,
        }
        if isinstance(restaurant, str) and restaurant:
            out[RESTAURANT_ID_HEADER] = restaurant
        if meta.is_retry and meta.retry_reason is not None:
            out[RETRY_REASON_HEADER] = RETRY_REASONS[meta.retry_reason]
        return out
