"""The webhook wire vocabulary: the event envelope, and the subscription shapes.

FOR: stating the documented notification envelope once, as models, so that the
event mapper composes a document rather than assembling a nested dictionary
literal -- and so that a field Square renames is a rename here rather than a
search across the mapper and its tests.

INVARIANT: **an order event carries a summary, not the order.** This is the
part of Square's envelope a rebuild gets wrong, because every other webhook
system puts the entity in ``data.object``. Square's documented
``order.created`` payload puts *five scalars* under a key named after
``data.type``::

    data: {type: "order_created", id: <order id>,
           object: {order_created: {created_at, location_id, order_id,
                                    state, version}}}

https://developer.squareup.com/reference/square/webhooks/order.created
https://developer.squareup.com/reference/square/webhooks/order.updated
(both fetched 2026-08-25). ``order.updated`` is the same with ``updated_at``
added, which is what makes a version bump observable to a consumer without a
re-read. The envelope itself -- ``merchant_id``, ``type``, ``event_id`` ("The
idempotency (UUID) value that uniquely identifies the event"), ``created_at``
(RFC 3339) and ``data`` -- is from
https://developer.squareup.com/docs/webhooks/build-with-webhooks.

Key order is the documented order, and it is not decoration: the delivered
bytes are what the signature covers, and ``dump_json`` preserves insertion
order, so a reordered model is a different signature over the same information.

SUBSCRIPTIONS. The stored record is the core's -- a subscriber registered
through Square's API and one registered through the control plane are the same
entity, which is what makes the dispatcher's view of the world single-valued.
The models here are only the request and response shapes for
https://developer.squareup.com/reference/square/webhook-subscriptions-api.
"""

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
"""Strict on the way out, as every other projection in this package is: a value
produced by this unit that has the wrong type is a defect here, and coercing it
would hide one."""

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)
"""Strict on the way in.

The reference reads ``spec.enabled === true``, so ``{"enabled": "yes"}`` is
silently ``false`` and a consumer who meant to register a live subscriber gets
a dead one with a 200. Strict validation turns that into ``invalid_value``
naming ``subscription.enabled``. ``extra="ignore"`` because Square's
``WebhookSubscription`` carries fields this unit does not model and refusing
the request over one of them would fail on the shrink rather than on the thing
under test.
"""


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
    """``data.object.order_updated`` -- the created summary plus ``updated_at``.

    Two models rather than one with an optional field, because the difference
    is the whole point: an ``order.updated`` that omitted ``updated_at`` would
    be indistinguishable on the wire from an ``order.created``, and a consumer
    keying on the presence of that field is doing exactly what Square's two
    documented examples invite.
    """

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
    """One entry of ``data.object.inventory_counts`` -- the ``InventoryCount``
    fields, in the documented order.
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
    #: Exactly one key, named by :attr:`type`. Built by the mapper, which is
    #: the only place that pairing is known.
    object: dict[str, Any]

    def wire(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "object": dict(self.object)}


class EventEnvelopeWire(BaseModel):
    """One notification, in the documented field order."""

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
    """The ``subscription`` object on CreateWebhookSubscription.

    ``event_types`` is required here and its *emptiness* is checked in the
    surface rather than with ``min_length``: an empty array and an absent key
    are the same mistake -- "you did not tell me what to send you" -- and
    Pydantic reports them as two different error types, which would surface as
    ``missing_field`` for one and ``invalid_value`` for the other.
    """

    model_config = _REQUEST

    notification_url: str = Field(min_length=1)
    event_types: list[str]
    name: str | None = None
    enabled: bool = True
    api_version: str | None = None


class CreateWebhookSubscriptionRequest(BaseModel):
    """``POST /v2/webhooks/subscriptions``.

    ``idempotency_key`` is optional, as Square documents it, and is read by the
    kernel through the route's :class:`~vendorfake.core.kernel.types.IdempotencySpec`
    rather than by the handler -- it is declared here only so that
    ``extra="ignore"`` does not have to carry it silently.
    """

    model_config = _REQUEST

    subscription: SubscriptionSpec
    idempotency_key: str | None = None


class TestWebhookSubscriptionRequest(BaseModel):
    """``POST /v2/webhooks/subscriptions/{subscription_id}/test``.

    Every field optional: Square documents ``event_type`` as "The event type
    that will be used to test the subscription", and omitting it is a
    legitimate request that means "use one this subscriber asked for".
    """

    model_config = _REQUEST

    event_type: str | None = None


class SubscriptionWire(BaseModel):
    """One ``WebhookSubscription``, as this unit publishes it.

    ``signature_key`` IS OPTIONAL HERE, because Square's own examples do not
    agree across the three responses that carry a subscription and this unit
    follows each of them:

    * CreateWebhookSubscription returns it --
      https://developer.squareup.com/reference/square/webhook-subscriptions-api/create-webhook-subscription
    * RetrieveWebhookSubscription returns it --
      https://developer.squareup.com/reference/square/webhook-subscriptions-api/retrieve-webhook-subscription
    * ListWebhookSubscriptions does **not**: its example response carries
      ``id``, ``name``, ``enabled``, ``event_types``, ``notification_url``,
      ``api_version``, ``created_at`` and ``updated_at``, and no key --
      https://developer.squareup.com/reference/square/webhook-subscriptions-api/list-webhook-subscriptions

    That distinction is worth keeping rather than smoothing over. A list is the
    one response that hands back subscribers the caller did not create -- a
    profile's ``webhooks.subscribers``, another consumer's registration -- and
    the signing key is the secret that makes a forged delivery verify. The
    caller learns their own key from the create call, which is where a consumer
    with a signature to verify actually gets it.

    ``compact`` drops the key when it is ``None``, so an omitted signing key is
    an absent field rather than a ``null`` a consumer's ``if "signature_key" in
    row`` would read as present.
    """

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
