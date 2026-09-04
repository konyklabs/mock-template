"""The dashboard stand-in: registering a callback, and the verification handshake.

CLOVER HAS NO SUBSCRIPTION API -- webhooks are configured in the dashboard
only (https://docs.clover.com/dev/docs/webhooks), so every route here is
this fake's own, under the ``/__clover/`` prefix.

``POST .../subscriptions`` inserts an unverified subscription and enqueues
its verification code; ``POST .../verify`` marks it verified, sets its event
types, and reveals the auth code later deliveries carry. A subscriber
created already holding an auth code is treated as pre-verified.

JUDGMENT, each labelled at its site: synthetic ids, auth-code timing, no
delete or re-send route here.

DOCUMENTED: HTTPS only ("Clover supports only HTTPS-enabled callbacks"),
overridable via ``allow_insecure_callbacks`` for a local receiver (JUDGMENT).

INVARIANT: these handlers touch only the core's ``SUBSCRIPTION_COLLECTION``.

(konyklabs/roadmap#38): route summaries are prefixed "Stand-in (not a Clover
endpoint)" since ``Route`` has no field for that.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from vendorfake.clover.events import VERIFICATION_EVENT_TYPE, event_type, verification_event_id
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.webhooks import (
    ALL_EVENT_KEYS,
    RegisterSubscriptionRequest,
    SubscriptionWire,
    VerifySubscriptionRequest,
)
from vendorfake.clover.surface.common import CloverDeps
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    PreparedEvent,
    ReplyInit,
    Route,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION, require_postable_target

__all__ = ["CAPABILITY", "PENDING_NAME", "CloverWebhooksSurface", "webhook_routes"]

CAPABILITY = "webhooks"

PENDING_NAME = "dashboard stand-in subscription"
"""The human-facing ``name`` given to a subscription registered here."""

STAND_IN = "Stand-in (not a Clover endpoint)"

REQUIRED_SCHEME = "https"

_VERIFIED = "verified"
_VERIFICATION_CODE = "verification_code"
_EVENT_KEYS = "event_keys"
"""Vendor-side fields on the core's subscription entity, ignored by
``Subscription.from_entity``."""


class CloverWebhooksSurface:
    """The three stand-in routes, bound to one vendor's id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `auth`: the dashboard is the developer's own console.
        return (
            Route(
                method="POST",
                path="/__clover/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.register,
                operation_id="RegisterWebhookCallback",
                summary=f"{STAND_IN}: register a callback URL in the dashboard and start its verification.",
            ),
            Route(
                method="GET",
                path="/__clover/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.list_subscriptions,
                operation_id="ListWebhookCallbacks",
                summary=f"{STAND_IN}: every registered callback and whether it is verified.",
            ),
            Route(
                method="POST",
                path="/__clover/webhooks/verify",
                capability=CAPABILITY,
                handler=self.verify,
                operation_id="VerifyWebhookCallback",
                summary=f"{STAND_IN}: paste the verification code into the dashboard, receive the auth code.",
            ),
        )

    # -- POST /__clover/webhooks/subscriptions -----------------------------

    def register(self, args: HandlerArgs) -> ReplyInit:
        """Insert a pending, unsubscribed subscription and send its
        verification code; ``verify`` fills in the event types. JUDGMENT:
        503 while ``webhooks.disable_delivery`` is set -- the code could
        never reach the callback."""
        ctx = args.ctx
        request = validate_body(RegisterSubscriptionRequest, args.body())
        self._require_https(request.url)
        require_postable_target(request.url, field="url")
        if not ctx.webhooks.enabled:
            raise UnitError(
                UnitErrorKind.UNAVAILABLE,
                detail=(
                    "Webhook delivery is disabled on this unit (webhooks.disable_delivery), so the verification "
                    "code could never reach the callback and this registration could never be verified. Register "
                    "a pre-verified subscriber through POST /__unit/webhooks/subscriptions instead."
                ),
                field="url",
            )
        ids = self._deps.ids
        subscription_id = ids.internal("wbhk")
        verification_code = ids.verification_code()
        auth_code = ids.webhook_auth_code()
        entity = ctx.store.collection(SUBSCRIPTION_COLLECTION).insert(
            {
                "id": subscription_id,
                "name": PENDING_NAME,
                "notification_url": request.url,
                "event_types": [],
                "signature_key": auth_code,
                "enabled": True,
                _VERIFIED: False,
                _VERIFICATION_CODE: verification_code,
                _EVENT_KEYS: list(request.eventKeys),
            },
            {"operation_id": "RegisterWebhookCallback"},
        )
        ctx.webhooks.enqueue_to(
            PreparedEvent(
                type=VERIFICATION_EVENT_TYPE,
                event_id=verification_event_id(subscription_id),
                entity_id=subscription_id,
                created_at=ctx.clock.iso_ms(),
                body={"verificationCode": verification_code},
            ),
            subscription_id,
        )
        return json_(_project(entity), 201)

    def _require_https(self, url: str) -> None:
        """DOCUMENTED -- Clover supports only HTTPS-enabled callbacks."""
        scheme = urlsplit(url).scheme.lower()
        if scheme == REQUIRED_SCHEME or self._deps.config.allow_insecure_callbacks:
            return
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"url must be an https:// callback: Clover supports only HTTPS-enabled callbacks "
                f"(https://docs.clover.com/dev/docs/webhooks). Got scheme {scheme or '(none)'!r}; set the "
                "vendor config's allow_insecure_callbacks to register a local http:// receiver against this fake."
            ),
            field="url",
        )

    # -- GET /__clover/webhooks/subscriptions ------------------------------

    def list_subscriptions(self, args: HandlerArgs) -> ReplyInit:
        """Every subscriber in the core's list, however it got there."""
        rows = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).all()
        return json_({"subscriptions": [_project(entity) for entity in rows]})

    # -- POST /__clover/webhooks/verify -------------------------------------

    def verify(self, args: HandlerArgs) -> ReplyInit:
        """Match the pasted code, activate the subscription, reveal the auth
        code. Idempotent once verified; a code matching nothing is a 400
        ``invalid_value`` on ``verificationCode``, not a 404."""
        ctx = args.ctx
        request = validate_body(VerifySubscriptionRequest, args.body())
        collection = ctx.store.collection(SUBSCRIPTION_COLLECTION)
        found = collection.find(lambda entity: entity.get(_VERIFICATION_CODE) == request.verificationCode)
        if found is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="verificationCode does not match any registered callback.",
                field="verificationCode",
            )
        if found.get(_VERIFIED) is not False:
            return json_(_project(found))
        keys = _event_keys(found)

        def activate(entity: dict[str, Any]) -> None:
            entity[_VERIFIED] = True
            entity["event_types"] = [event_type(key, "*") for key in keys]

        updated = collection.update(str(found["id"]), activate, meta={"operation_id": "VerifyWebhookCallback"})
        return json_(_project(updated))


def webhook_routes(deps: CloverDeps) -> tuple[Route, ...]:
    """The dashboard stand-in routes for one vendor."""
    return CloverWebhooksSurface(deps).routes()


def _event_keys(entity: Mapping[str, Any]) -> tuple[str, ...]:
    """Own ``event_keys`` if registered here, else derived from
    ``event_types`` patterns."""
    stored = entity.get(_EVENT_KEYS)
    if isinstance(stored, list):
        return tuple(str(key) for key in stored)
    patterns = entity.get("event_types", ())
    if not isinstance(patterns, list):
        return ()
    if "*" in patterns:
        return ALL_EVENT_KEYS
    prefixes = {str(pattern).split(":", 1)[0] for pattern in patterns}
    return tuple(key for key in ALL_EVENT_KEYS if key in prefixes)


def _project(entity: Mapping[str, Any]) -> dict[str, Any]:
    """One stored subscription; the auth code is ``signature_key``, shown
    only once verified."""
    verified = entity.get(_VERIFIED) is not False
    return SubscriptionWire(
        id=str(entity["id"]),
        url=str(entity.get("notification_url", "")),
        eventKeys=list(_event_keys(entity)),
        verified=verified,
        authCode=str(entity.get("signature_key", "")) if verified else None,
    ).wire()
