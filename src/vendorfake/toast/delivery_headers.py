"""The non-signature headers on a Toast delivery.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiHttpHeaders.html), and
emitted here in the documented casing:

* ``Toast-Attempt-Number`` -- starts at 1, on every attempt;
* ``Toast-Event-Type`` -- e.g. ``order_updated``, ``in_stock``;
* ``Toast-Event-Category`` -- e.g. ``stock``;
* ``Toast-Restaurant-External-ID`` -- "omitted if not restaurant-scoped";
* ``Content-Type: application/json``.

The page also lists ``Content-Length``, ``Accept-Encoding``, ``Connection``
and ``User-Agent``: transport headers the delivery sink owns, not this unit.

The three Toast-* values are read from the delivered body -- the envelope's
``eventType``, ``eventCategory`` and ``details.restaurantGuid`` -- so the
headers and the body can never disagree. A body that is not a Toast envelope
(the control plane's emitter accepts any document) gets the core event type in
both ``Toast-Event-*`` headers and no restaurant header.

JUDGMENT -- **one header is this fake's.** ``x-vendorfake-retry-reason``
appears on a retry only, carrying the core's neutral outcome name: Toast
documents no retry-only header, and the conformance suite's C16 asks that a
retry be distinguishable from a first send by a header the vendor names.
``Toast-Attempt-Number`` changes value on a retry but is present on the first
send too, so it cannot be that header. The product's own prefix is the one a
consumer cannot mistake for Toast's; a handler must not depend on it.
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
