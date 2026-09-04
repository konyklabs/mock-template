"""The webhook wire vocabulary: the event envelope, and the subscription shapes. DOCUMENTED -- an
order event carries a summary, not the order: ``order.created`` puts five scalars under a key named
after ``data.type``; ``order.updated`` adds ``updated_at``.
https://developer.squareup.com/reference/square/webhooks/order.created https://developer.squareup.com/reference/square/webhooks/order.updated
INVARIANT -- key order is the documented order, since the delivered bytes are what the signature covers.
https://developer.squareup.com/reference/square/webhook-subscriptions-api"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact

__all__ = [
    "CatalogVersionUpdatedSummary",
    "CreateWebhookSubscriptionRequest",
    "EventDataWire",
    "EventEnvelopeWire",
    "InventoryCountSummary",
    "OrderCreatedSummary",
    "OrderUpdatedSummary",
    "SubscriptionSpec",
    "SubscriptionWire",
    "TestWebhookSubscriptionRequest",
]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Strict on the way out: a wrong-typed value this unit produces is a defect, not something to coerce."""

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)
"""Strict on the way in, so ``{"enabled": "yes"}`` is ``invalid_value``, not silently coerced.
``extra="ignore"`` since Square's ``WebhookSubscription`` carries fields this unit does not model."""


# ---------------------------------------------------------------------------
# The notification envelope.
# ---------------------------------------------------------------------------


class OrderCreatedSummary(BaseModel):
    """``data.object.order_created`` -- five scalars, not the order."""

    model_config = _WIRE

    created_at: str
    location_id: str
    order_id: str
    state: str
    version: int

    def wire(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "location_id": self.location_id,
            "order_id": self.order_id,
            "state": self.state,
            "version": self.version,
        }


class OrderUpdatedSummary(BaseModel):
    """``data.object.order_updated`` -- the created summary plus ``updated_at``. Two models rather
    than one optional field: an ``order.updated`` missing it would be indistinguishable from a create."""

    model_config = _WIRE

    created_at: str
    location_id: str
    order_id: str
    state: str
    updated_at: str
    version: int

    def wire(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "location_id": self.location_id,
            "order_id": self.order_id,
            "state": self.state,
            "updated_at": self.updated_at,
            "version": self.version,
        }


class CatalogVersionUpdatedSummary(BaseModel):
    """``data.object.catalog_version`` -- one field, when the catalog changed.
    https://developer.squareup.com/reference/square/webhooks/catalog.version.updated
    """

    model_config = _WIRE

    updated_at: str

    def wire(self) -> dict[str, Any]:
        return {"updated_at": self.updated_at}


class InventoryCountSummary(BaseModel):
    """One entry of ``data.object.inventory_counts``, in the documented field order.
    https://developer.squareup.com/reference/square/webhooks/inventory.count.updated
    """

    model_config = _WIRE

    calculated_at: str
    catalog_object_id: str
    catalog_object_type: str
    location_id: str
    quantity: str
    state: str

    def wire(self) -> dict[str, Any]:
        return {
            "calculated_at": self.calculated_at,
            "catalog_object_id": self.catalog_object_id,
            "catalog_object_type": self.catalog_object_type,
            "location_id": self.location_id,
            "quantity": self.quantity,
            "state": self.state,
        }


class EventDataWire(BaseModel):
    """``data``: the type, the entity id, and the one-key object above."""

    model_config = _WIRE

    type: str
    id: str
    #: Exactly one key, named by :attr:`type`; built by the mapper.
    object: dict[str, Any]

    def wire(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "object": dict(self.object)}


class EventEnvelopeWire(BaseModel):
    """One notification, in the documented field order.
    https://developer.squareup.com/docs/webhooks/build-with-webhooks
    """

    model_config = _WIRE

    merchant_id: str
    type: str
    event_id: str
    created_at: str
    data: EventDataWire

    def wire(self) -> dict[str, Any]:
        return {
            "merchant_id": self.merchant_id,
            "type": self.type,
            "event_id": self.event_id,
            "created_at": self.created_at,
            "data": self.data.wire(),
        }


# ---------------------------------------------------------------------------
# Subscriptions.
# ---------------------------------------------------------------------------


class SubscriptionSpec(BaseModel):
    """The ``subscription`` object on CreateWebhookSubscription. ``event_types`` emptiness is checked in
    the surface, not with ``min_length`` -- Pydantic treats an empty array and an absent key differently."""

    model_config = _REQUEST

    notification_url: str = Field(min_length=1)
    event_types: list[str]
    name: str | None = None
    enabled: bool = True
    api_version: str | None = None


class CreateWebhookSubscriptionRequest(BaseModel):
    """``POST /v2/webhooks/subscriptions``. ``idempotency_key`` is read by the kernel through the
    route's :class:`~vendorfake.core.kernel.types.IdempotencySpec`, declared here only so
    ``extra="ignore"`` need not carry it silently."""

    model_config = _REQUEST

    subscription: SubscriptionSpec
    idempotency_key: str | None = None


class TestWebhookSubscriptionRequest(BaseModel):
    """``POST /v2/webhooks/subscriptions/{subscription_id}/test``. Every field
    optional: an omitted ``event_type`` means "use one this subscriber asked for"."""

    model_config = _REQUEST

    event_type: str | None = None


class SubscriptionWire(BaseModel):
    """One ``WebhookSubscription``, as this unit publishes it. DOCUMENTED -- ``signature_key`` is optional
    here because Square's own examples disagree: Create and Retrieve return it, List does not.
    https://developer.squareup.com/reference/square/webhook-subscriptions-api/create-webhook-subscription https://developer.squareup.com/reference/square/webhook-subscriptions-api/retrieve-webhook-subscription
    https://developer.squareup.com/reference/square/webhook-subscriptions-api/list-webhook-subscriptions https://developer.squareup.com/reference/square/webhook-subscriptions-api"""

    model_config = _WIRE

    id: str
    name: str
    enabled: bool
    event_types: list[str]
    notification_url: str
    signature_key: str | None = None
    api_version: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "enabled": self.enabled,
                "event_types": list(self.event_types),
                "notification_url": self.notification_url,
                "api_version": self.api_version,
                "signature_key": self.signature_key,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )
