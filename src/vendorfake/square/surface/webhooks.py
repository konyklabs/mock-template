"""The Webhook Subscriptions surface: registering a subscriber through Square's own API rather than a control
channel. https://developer.squareup.com/reference/square/webhook-subscriptions-api

INVARIANT: there is one subscription list, owned by the core; these handlers are pure shape translation over the
core's ``subscriptions`` collection, the same one the dispatcher fans out to and
``POST /__unit/webhooks/subscriptions`` writes. All six routes require
:data:`~vendorfake.square.config.WEBHOOK_SUBSCRIPTIONS_SCOPE` -- application-owned, per
https://developer.squareup.com/docs/webhooks/webhook-subscriptions-api (see that constant for the
fuller citation);
``tests/unit/test_route_scopes.py`` fails any route of any vendor that authenticates without one.

JUDGMENT: ``POST .../test`` waits under the request lock for the delivery worker's first attempt (bounded by the
delivery timeout) and reports that attempt, not the eventual outcome -- Square documents the response shape but
not the wait.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    IdempotencySpec,
    PreparedEvent,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.webhooks.models import (
    SUBSCRIPTION_COLLECTION,
    Subscription,
    matches_event_type,
    require_postable_target,
)
from vendorfake.square.config import WEBHOOK_SUBSCRIPTIONS_SCOPE
from vendorfake.square.events import ORDER_CREATED, SQUARE_EVENT_TYPES
from vendorfake.square.model.common import validate_body
from vendorfake.square.model.webhooks import (
    CreateWebhookSubscriptionRequest,
    SubscriptionWire,
    TestWebhookSubscriptionRequest,
)
from vendorfake.square.surface.common import SquareDeps

__all__ = ["CAPABILITY", "DEFAULT_SUBSCRIPTION_NAME", "WebhooksSurface", "webhook_routes"]

CAPABILITY = "webhooks"
"""The capability every route below belongs to."""

DEFAULT_SUBSCRIPTION_NAME = "Subscription"
"""What an unnamed subscriber is called; a name is for a human reading a list, so an absent one gets a placeholder
rather than an absent key."""

_RELEASE_STATUS = "PUBLIC"
"""``EventTypeMetadata.release_status``; every type this unit emits is generally available, never ``BETA``."""


class WebhooksSurface:
    """The six Webhook Subscriptions routes, bound to one vendor's id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        """Literal paths first, then the parameterised ones: the router matches in order, so
        ``/v2/webhooks/event-types`` must not sit behind a hypothetical ``/v2/webhooks/{something}``."""
        return (
            Route(
                method="GET",
                path="/v2/webhooks/event-types",
                capability=CAPABILITY,
                handler=self.list_event_types,
                auth="bearer",
                scopes=(WEBHOOK_SUBSCRIPTIONS_SCOPE,),
                operation_id="ListWebhookEventTypes",
                summary="Event types this unit can emit.",
            ),
            Route(
                method="POST",
                path="/v2/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.create_subscription,
                auth="bearer",
                scopes=(WEBHOOK_SUBSCRIPTIONS_SCOPE,),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="webhooks.create"),
                example_body={
                    "subscription": {
                        "notification_url": "https://example-consumer.test/hooks",
                        "event_types": ["order.created"],
                    }
                },
                operation_id="CreateWebhookSubscription",
                summary="Register a subscriber and receive its signature key.",
            ),
            Route(
                method="GET",
                path="/v2/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.list_subscriptions,
                auth="bearer",
                scopes=(WEBHOOK_SUBSCRIPTIONS_SCOPE,),
                operation_id="ListWebhookSubscriptions",
                summary="List subscribers.",
            ),
            Route(
                method="GET",
                path="/v2/webhooks/subscriptions/{subscription_id}",
                capability=CAPABILITY,
                handler=self.retrieve_subscription,
                auth="bearer",
                scopes=(WEBHOOK_SUBSCRIPTIONS_SCOPE,),
                operation_id="RetrieveWebhookSubscription",
                summary="Retrieve one subscriber.",
            ),
            Route(
                method="DELETE",
                path="/v2/webhooks/subscriptions/{subscription_id}",
                capability=CAPABILITY,
                handler=self.delete_subscription,
                auth="bearer",
                scopes=(WEBHOOK_SUBSCRIPTIONS_SCOPE,),
                operation_id="DeleteWebhookSubscription",
                summary="Remove a subscriber.",
            ),
            Route(
                method="POST",
                path="/v2/webhooks/subscriptions/{subscription_id}/test",
                capability=CAPABILITY,
                handler=self.test_subscription,
                auth="bearer",
                scopes=(WEBHOOK_SUBSCRIPTIONS_SCOPE,),
                operation_id="TestWebhookSubscription",
                summary="Send a signed test event and report the subscriber status code.",
            ),
        )

    # -- GET /v2/webhooks/event-types --------------------------------------

    def list_event_types(self, args: HandlerArgs) -> ReplyInit:
        """What this unit can send, and from which API version. JUDGMENT: ``api_version_introduced`` carries this
        unit's own configured version, not the version Square introduced each type in -- claiming Square's history
        would be an unsourced fact."""
        api_version = args.ctx.vendor.api_version
        return json_(
            {
                "event_types": list(SQUARE_EVENT_TYPES),
                "metadata": [
                    {
                        "event_type": event_type,
                        "api_version_introduced": api_version,
                        "release_status": _RELEASE_STATUS,
                    }
                    for event_type in SQUARE_EVENT_TYPES
                ],
            }
        )

    # -- POST /v2/webhooks/subscriptions -----------------------------------

    def create_subscription(self, args: HandlerArgs) -> ReplyInit:
        """Register a subscriber and mint its signature key here: the only way a consumer can verify what this unit
        sends, since Square shows the key on creation only and never again.
        See :class:`~vendorfake.square.model.webhooks.SubscriptionWire`."""
        request = validate_body(CreateWebhookSubscriptionRequest, args.body())
        spec = request.subscription
        if not spec.event_types:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="event_types is required.",
                field="subscription.event_types",
            )
        require_postable_target(spec.notification_url, field="subscription.notification_url")
        entity = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).insert(
            {
                "id": self._deps.ids.subscription(),
                "name": spec.name or DEFAULT_SUBSCRIPTION_NAME,
                "notification_url": spec.notification_url,
                "event_types": list(spec.event_types),
                "signature_key": self._deps.ids.signature_key(),
                "enabled": spec.enabled,
                "api_version": spec.api_version or args.ctx.vendor.api_version,
            },
            {"operation_id": "CreateWebhookSubscription"},
        )
        return json_({"subscription": _project(entity, signature_key=True)})

    # -- GET /v2/webhooks/subscriptions ------------------------------------

    def list_subscriptions(self, args: HandlerArgs) -> ReplyInit:
        """Every subscriber, however it was registered. Reads through the store rather than
        ``ctx.webhooks.subscriptions()``, which drops the store's ``created_at``/``updated_at`` stamps.
        No ``signature_key`` here, unlike create and retrieve: Square's own list example omits it while
        create/retrieve include it. See :class:`SubscriptionWire`."""
        rows = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).all()
        return json_({"subscriptions": [_project(entity, signature_key=False) for entity in rows]})

    # -- GET /v2/webhooks/subscriptions/{subscription_id} -------------------

    def retrieve_subscription(self, args: HandlerArgs) -> ReplyInit:
        """One subscriber, with its signing key: Square's example response for this operation carries
        ``signature_key``, unlike its list example.
        https://developer.squareup.com/reference/square/webhook-subscriptions-api/retrieve-webhook-subscription"""
        entity = _require_subscription(args.ctx, args.params["subscription_id"])
        return json_({"subscription": _project(entity, signature_key=True)})

    # -- DELETE /v2/webhooks/subscriptions/{subscription_id} ----------------

    def delete_subscription(self, args: HandlerArgs) -> ReplyInit:
        """Remove a subscriber. 404 first, so a repeated delete is not a 200; Square's DeleteWebhookSubscription
        returns an empty object on success."""
        subscription_id = args.params["subscription_id"]
        _require_subscription(args.ctx, subscription_id)
        args.ctx.store.collection(SUBSCRIPTION_COLLECTION).delete(
            subscription_id, {"operation_id": "DeleteWebhookSubscription"}
        )
        return json_({})

    # -- POST /v2/webhooks/subscriptions/{subscription_id}/test -------------

    def test_subscription(self, args: HandlerArgs) -> ReplyInit:
        """Send one synthetic event down the real delivery path (prepare, sign, deliver) so a consumer's endpoint
        sees a genuinely signed request; only the payload is synthetic (``data.type`` is ``test``).
        The event id is ``evt_test_<n>`` rather than a minted one, so it is greppable in a consumer's log while
        they wire up their handler, without moving the id stream's subsequent entries."""
        ctx = args.ctx
        subscription = Subscription.from_entity(_require_subscription(ctx, args.params["subscription_id"]))
        request = validate_body(TestWebhookSubscriptionRequest, args.body())
        event_type = request.event_type or _first_event_type(subscription)
        before = len(ctx.webhooks.deliveries())
        event_id = f"evt_test_{before + 1}"
        created_at = ctx.clock.iso_ms()
        queued = ctx.webhooks.enqueue_to(
            PreparedEvent(
                type=event_type,
                event_id=event_id,
                entity_id=subscription.id,
                created_at=created_at,
                body={
                    "merchant_id": "TEST_MERCHANT",
                    "type": event_type,
                    "event_id": event_id,
                    "created_at": created_at,
                    "data": {"type": "test", "id": subscription.id, "object": {"test": True}},
                },
            ),
            subscription.id,
        )
        attempt = (
            ctx.webhooks.await_first_attempt(
                event_id, subscription.id, timeout_ms=float(ctx.webhooks.retry_policy.timeout_ms)
            )
            if queued
            else None
        )
        now = ctx.clock.iso_ms()
        return json_(
            {
                "subscription_test_result": {
                    "id": event_id,
                    # 0 means nothing was recorded: no answer inside the timeout, or a chaos rule dropped the delivery.
                    "status_code": 0 if attempt is None else attempt.response_status,
                    "payload": "" if attempt is None else attempt.body_preview,
                    "created_at": now,
                    "updated_at": now,
                }
            }
        )


def webhook_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The Webhook Subscriptions routes for one vendor."""
    return WebhooksSurface(deps).routes()


def _first_event_type(subscription: Subscription) -> str:
    """The type a test event takes when the caller names none: a type the subscriber asked for, so a consumer's
    dispatch on ``body["type"]`` recognises it.
    ``event_types`` holds patterns (``*``, ``order.*``), not literal types, so the first advertised type a pattern
    covers is used rather than the pattern itself; falls back to :data:`~vendorfake.square.events.ORDER_CREATED`."""
    for pattern in subscription.event_types:
        if pattern in SQUARE_EVENT_TYPES:
            return pattern
    for event_type in SQUARE_EVENT_TYPES:
        if matches_event_type(subscription.event_types, event_type):
            return event_type
    return ORDER_CREATED


def _require_subscription(ctx: UnitContext, subscription_id: str) -> Mapping[str, Any]:
    entity = ctx.store.collection(SUBSCRIPTION_COLLECTION).get(subscription_id)
    if entity is None:
        raise UnitError(
            UnitErrorKind.NOT_FOUND,
            detail=f"Webhook subscription {subscription_id} was not found.",
            field="subscription_id",
        )
    return entity


def _project(entity: Mapping[str, Any], *, signature_key: bool) -> dict[str, Any]:
    """One stored subscription as Square's ``WebhookSubscription``. ``signature_key`` is a required keyword so a
    future route returning a subscription must decide about the signing key explicitly.
    Reads through the core's typed :class:`Subscription` so this projection and the dispatcher's fan-out cannot
    disagree about ``event_types``/``enabled``; the two timestamps come from the store, not this vendor."""
    subscription = Subscription.from_entity(entity)
    created_at = entity.get("created_at")
    updated_at = entity.get("updated_at")
    return SubscriptionWire(
        id=subscription.id,
        name=subscription.name or DEFAULT_SUBSCRIPTION_NAME,
        enabled=subscription.enabled,
        event_types=list(subscription.event_types),
        notification_url=subscription.notification_url,
        signature_key=subscription.signature_key if signature_key else None,
        api_version=subscription.api_version,
        created_at=None if created_at is None else str(created_at),
        updated_at=None if updated_at is None else str(updated_at),
    ).wire()
