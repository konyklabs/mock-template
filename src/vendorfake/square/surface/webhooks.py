"""The Webhook Subscriptions surface: registering a subscriber through Square's
own API rather than through a control channel.

FOR: letting a consumer's integration code do the thing it does against Square
-- POST a subscription, receive a signature key, verify the deliveries that
follow -- instead of being told to configure the fake out of band.

https://developer.squareup.com/reference/square/webhook-subscriptions-api

=============================  ==================================================
CreateWebhookSubscription      ``POST   /v2/webhooks/subscriptions``
ListWebhookSubscriptions       ``GET    /v2/webhooks/subscriptions``
RetrieveWebhookSubscription    ``GET    /v2/webhooks/subscriptions/{subscription_id}``
DeleteWebhookSubscription      ``DELETE /v2/webhooks/subscriptions/{subscription_id}``
TestWebhookSubscription        ``POST   /v2/webhooks/subscriptions/{subscription_id}/test``
ListWebhookEventTypes          ``GET    /v2/webhooks/event-types``
=============================  ==================================================

INVARIANT: **there is one subscription list, and the core owns it.** These
handlers are pure shape translation over the core's ``subscriptions``
collection, which is the same collection ``POST /__unit/webhooks/subscriptions``
writes and the same one the dispatcher fans out to. A vendor that kept its own
list would give a consumer two ways to register a subscriber and one of them
would not receive anything.

That also means registering a subscriber journals like any other mutation --
and the dispatcher deliberately ignores journal entries for this collection, so
subscribing does not notify every subscriber that somebody subscribed.

``POST .../test`` DECLARES ``serialized=False``
-----------------------------------------------
It is the one vendor route that blocks inside its handler on machinery another
request must feed: it enqueues an event and then waits for the delivery worker.
Under the pipeline's request lock that would hold the entire unit for the
delivery timeout times the retry schedule against an unreachable subscriber.
The reference gets away with it because Node's loop yields at its ``await``; a
lock does not. See ``Route.serialized`` and ``WebhookDispatcher.drain``.

JUDGMENT -- **the test route waits for one delivery timeout, not for the whole
cascade.** Square documents TestWebhookSubscription as sending a test event and
reporting the subscriber's status code, and publishes nothing about how long it
waits. Draining without a bound would make this endpoint hang for
``timeout_ms`` times eleven retries against a dead URL, which is a worse answer
than the ``status_code: 0`` the reference already produces for "no attempt was
recorded". Retries continue in the background and show up at
``GET /__unit/webhooks/deliveries`` either way.

SHRINK (prototype): ``UpdateWebhookSubscription`` (PUT), the enabled/disabled
toggle endpoint and ``UpdateWebhookSubscriptionSignatureKey`` are not
implemented. None of them changes delivery behaviour, and a rotated signature
key is a fixture change a consumer can make by registering a second
subscriber.
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
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION, Subscription
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
"""What an unnamed subscriber is called. Square's ``name`` is optional and its
own examples call one "Example Webhook Subscription"; a name is for a human
reading a list, so an absent one gets a placeholder rather than an absent key."""

_RELEASE_STATUS = "PUBLIC"
"""``EventTypeMetadata.release_status``. Every type this unit emits is a
generally available Square event, so none of them is ``BETA``."""


class WebhooksSurface:
    """The six Webhook Subscriptions routes, bound to one vendor's id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        """Literal paths first, then the parameterised ones.

        The router matches in order, so ``/v2/webhooks/event-types`` must not
        sit behind a hypothetical ``/v2/webhooks/{something}``. There is no
        such route today; the ordering is what keeps adding one from silently
        shadowing this one.
        """
        return (
            Route(
                method="GET",
                path="/v2/webhooks/event-types",
                capability=CAPABILITY,
                handler=self.list_event_types,
                auth="bearer",
                operation_id="ListWebhookEventTypes",
                summary="Event types this unit can emit.",
            ),
            Route(
                method="POST",
                path="/v2/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.create_subscription,
                auth="bearer",
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="webhooks.create"),
                operation_id="CreateWebhookSubscription",
                summary="Register a subscriber and receive its signature key.",
            ),
            Route(
                method="GET",
                path="/v2/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.list_subscriptions,
                auth="bearer",
                operation_id="ListWebhookSubscriptions",
                summary="List subscribers.",
            ),
            Route(
                method="GET",
                path="/v2/webhooks/subscriptions/{subscription_id}",
                capability=CAPABILITY,
                handler=self.retrieve_subscription,
                auth="bearer",
                operation_id="RetrieveWebhookSubscription",
                summary="Retrieve one subscriber.",
            ),
            Route(
                method="DELETE",
                path="/v2/webhooks/subscriptions/{subscription_id}",
                capability=CAPABILITY,
                handler=self.delete_subscription,
                auth="bearer",
                operation_id="DeleteWebhookSubscription",
                summary="Remove a subscriber.",
            ),
            Route(
                method="POST",
                path="/v2/webhooks/subscriptions/{subscription_id}/test",
                capability=CAPABILITY,
                handler=self.test_subscription,
                auth="bearer",
                operation_id="TestWebhookSubscription",
                summary="Send a signed test event and report the subscriber status code.",
                # Blocks on the delivery worker; see the module docstring.
                serialized=False,
            ),
        )

    # -- GET /v2/webhooks/event-types --------------------------------------

    def list_event_types(self, args: HandlerArgs) -> ReplyInit:
        """What this unit can send, and from which API version.

        ``api_version_introduced`` is this unit's own version rather than the
        version Square introduced each type in: a fake that claimed
        ``2021-05-13`` would be asserting a fact about Square's history it has
        no source for. JUDGMENT, and it is why the field carries the unit's
        configured version instead.
        """
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
        """Register a subscriber and mint its signature key.

        The key is minted here and returned, which is the only way a consumer
        can verify what this unit sends. Square returns it on creation too, and
        the difference is that Square never shows it again -- see
        :class:`~vendorfake.square.model.webhooks.SubscriptionWire`.
        """
        request = validate_body(CreateWebhookSubscriptionRequest, args.body())
        spec = request.subscription
        if not spec.event_types:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="event_types is required.",
                field="subscription.event_types",
            )
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
        return json_({"subscription": _project(entity)})

    # -- GET /v2/webhooks/subscriptions ------------------------------------

    def list_subscriptions(self, args: HandlerArgs) -> ReplyInit:
        """Every subscriber, however it was registered.

        Read through the store rather than through
        ``ctx.webhooks.subscriptions()`` -- which returns the dispatcher's
        typed view and drops the store's ``created_at``/``updated_at`` stamps
        that Square's object carries.
        """
        rows = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).all()
        return json_({"subscriptions": [_project(entity) for entity in rows]})

    # -- GET /v2/webhooks/subscriptions/{subscription_id} -------------------

    def retrieve_subscription(self, args: HandlerArgs) -> ReplyInit:
        entity = _require_subscription(args.ctx, args.params["subscription_id"])
        return json_({"subscription": _project(entity)})

    # -- DELETE /v2/webhooks/subscriptions/{subscription_id} ----------------

    def delete_subscription(self, args: HandlerArgs) -> ReplyInit:
        """Remove a subscriber. 404 first, so a repeated delete is not a 200.

        Square's DeleteWebhookSubscription returns an empty object on success;
        the ``errors`` array is what a failure carries.
        """
        subscription_id = args.params["subscription_id"]
        _require_subscription(args.ctx, subscription_id)
        args.ctx.store.collection(SUBSCRIPTION_COLLECTION).delete(
            subscription_id, {"operation_id": "DeleteWebhookSubscription"}
        )
        return json_({})

    # -- POST /v2/webhooks/subscriptions/{subscription_id}/test -------------

    def test_subscription(self, args: HandlerArgs) -> ReplyInit:
        """Send one synthetic event down the real delivery path and report back.

        The *real* path: the same prepare, sign and deliver the journal drives,
        so a consumer's endpoint sees a genuinely signed request with the same
        headers a live event would carry. Only the payload is synthetic, and it
        says so -- ``data.type`` is ``test``.

        The event id is ``evt_test_<n>`` rather than a minted one: it is not
        derived from a journal entry, it must be greppable in a consumer's log
        while they are wiring up their handler, and drawing from the id stream
        would move every subsequent entity id in the scenario.
        """
        ctx = args.ctx
        subscription = Subscription.from_entity(_require_subscription(ctx, args.params["subscription_id"]))
        request = validate_body(TestWebhookSubscriptionRequest, args.body())
        event_type = request.event_type or _first_event_type(subscription)
        before = len(ctx.webhooks.deliveries())
        event_id = f"evt_test_{before + 1}"
        created_at = ctx.clock.iso_ms()
        ctx.webhooks.enqueue(
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
            )
        )
        ctx.webhooks.drain(timeout_ms=float(ctx.webhooks.retry_policy.timeout_ms))
        attempt = next((d for d in ctx.webhooks.deliveries() if d.event_id == event_id), None)
        now = ctx.clock.iso_ms()
        return json_(
            {
                "subscription_test_result": {
                    "id": event_id,
                    # 0 when nothing was recorded: the subscriber did not
                    # answer inside the delivery timeout, or a chaos rule
                    # dropped the delivery before it reached the sink.
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
    """The type a test event takes when the caller names none.

    The subscriber's own first type, so the test event actually reaches it --
    sending ``order.created`` to a subscriber that asked only for
    ``order.updated`` would be filtered out and reported as ``status_code: 0``,
    which reads as "your endpoint is down".
    """
    return subscription.event_types[0] if subscription.event_types else ORDER_CREATED


def _require_subscription(ctx: UnitContext, subscription_id: str) -> Mapping[str, Any]:
    entity = ctx.store.collection(SUBSCRIPTION_COLLECTION).get(subscription_id)
    if entity is None:
        raise UnitError(
            UnitErrorKind.NOT_FOUND,
            detail=f"Webhook subscription {subscription_id} was not found.",
            field="subscription_id",
        )
    return entity


def _project(entity: Mapping[str, Any]) -> dict[str, Any]:
    """One stored subscription as Square's ``WebhookSubscription``.

    Goes through the core's typed :class:`Subscription` reader for everything
    the dispatcher also reads, so the projection and the fan-out cannot
    disagree about what ``event_types`` or ``enabled`` mean, and reads the two
    store stamps off the entity -- they belong to the store, not to this
    vendor, exactly as they do for a location.
    """
    subscription = Subscription.from_entity(entity)
    created_at = entity.get("created_at")
    updated_at = entity.get("updated_at")
    return SubscriptionWire(
        id=subscription.id,
        name=subscription.name or DEFAULT_SUBSCRIPTION_NAME,
        enabled=subscription.enabled,
        event_types=list(subscription.event_types),
        notification_url=subscription.notification_url,
        signature_key=subscription.signature_key,
        api_version=subscription.api_version,
        created_at=None if created_at is None else str(created_at),
        updated_at=None if updated_at is None else str(updated_at),
    ).wire()
