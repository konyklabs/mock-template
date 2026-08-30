"""The webhook wire vocabulary: the documented envelope, the category
vocabulary, and the subscription stand-in's shapes.

THE ENVELOPE is DOCUMENTED verbatim
(https://doc.toasttab.com/doc/devguide/apiMessageDataSchema.html)::

    {"timestamp": "2024-03-28T15:11:01.050Z", "eventCategory": "...",
     "eventType": "...", "guid": "<eventGuid>", "details": {...}}

THE CATEGORIES this unit emits, and their documented types
(devOrdersWebhookRef.html, apiStockWebhook.html, the menus webhook page):

* ``order_updated`` -- ``eventCategory`` and ``eventType`` are both
  ``order_updated``; ``details`` is ``{restaurantGuid, order}`` with the full
  Order as ``GET /orders/{guid}`` returns it; "A new order is also considered
  an update";
* ``stock`` -- ``in_stock``, ``out_of_stock``, ``low_quantity``; ``details``
  is ``{itemGuid, restaurantGuid, status, multiLocationId, versionId}`` plus
  ``quantity`` for ``QUANTITY`` rows;
* ``menus`` -- ``menus_updated``; ``details`` is ``{restaurantGuid, publishedDate}``.

On the core's side an event's type is the Toast ``eventType`` string, and a
subscription "to a category" is the core's list of that category's types
(:data:`CATEGORY_TYPES`) -- so the dispatcher's own matcher does the filtering.

THE SUBSCRIPTION SHAPES are JUDGMENT: Toast has no subscription API (two
official pages disagree only on whether the integrations team or the developer
portal creates one -- audit gap 10), so the request a consumer sends to the
stand-in, and the record it gets back, are this project's: a callback URL,
the categories, and the per-subscription secret Toast documents as "the
webhook secret".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vendorfake.core.util.json import compact

__all__ = [
    "ALL_CATEGORIES",
    "CATEGORY_TYPES",
    "EnvelopeWire",
    "RegisterSubscriptionRequest",
    "SubscriptionWire",
    "category_of",
]

CATEGORY_TYPES: Mapping[str, tuple[str, ...]] = {
    "order_updated": ("order_updated",),
    "stock": ("in_stock", "out_of_stock", "low_quantity"),
    "menus": ("menus_updated",),
}
"""Documented category -> its documented event types."""

ALL_CATEGORIES: tuple[str, ...] = tuple(CATEGORY_TYPES)


def category_of(event_type: str) -> str | None:
    for category, types in CATEGORY_TYPES.items():
        if event_type in types:
            return category
    return None


_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)
_REQUEST = ConfigDict(extra="ignore", frozen=True)


class EnvelopeWire(BaseModel):
    """The documented envelope; ``details`` is the category's document."""

    model_config = _WIRE

    timestamp: str
    eventCategory: str
    eventType: str
    guid: str
    details: dict[str, Any]

    def wire(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "eventCategory": self.eventCategory,
            "eventType": self.eventType,
            "guid": self.guid,
            "details": dict(self.details),
        }


class RegisterSubscriptionRequest(BaseModel):
    """``POST /__toast/webhooks/subscriptions``: a callback and its categories.

    ``secret`` is optional: Toast generates one per subscription and shows it
    in the portal, so an omitted one is minted; a supplied one is kept so a
    consumer can pin the value their receiver is configured with.
    """

    model_config = _REQUEST

    url: str = Field(min_length=1)
    eventCategories: tuple[str, ...] = ALL_CATEGORIES
    secret: str | None = Field(default=None, min_length=1)

    @field_validator("eventCategories")
    @classmethod
    def _known(cls, categories: tuple[str, ...]) -> tuple[str, ...]:
        if not categories:
            raise ValueError("must name at least one event category")
        unknown = [c for c in categories if c not in CATEGORY_TYPES]
        if unknown:
            raise ValueError(f"unknown event category(s) {unknown}; this unit emits {list(ALL_CATEGORIES)}")
        return tuple(c for c in ALL_CATEGORIES if c in categories)


class SubscriptionWire(BaseModel):
    model_config = _WIRE

    guid: str
    url: str
    eventCategories: list[str]
    secret: str
    enabled: bool

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "guid": self.guid,
                "url": self.url,
                "eventCategories": list(self.eventCategories),
                "secret": self.secret,
                "enabled": self.enabled,
            }
        )
