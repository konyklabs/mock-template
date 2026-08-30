"""The dashboard stand-in: registering a callback, and the verification handshake.

FOR: giving a consumer a way to do, against this unit, the thing they do in
Clover's developer dashboard -- enter a callback URL, receive a verification
code at it, paste the code back, receive the auth code -- so that the
handshake their integration must survive is rehearsed rather than skipped.

CLOVER HAS NO SUBSCRIPTION API. Webhooks are configured in the dashboard only
(https://docs.clover.com/dev/docs/webhooks, fetched 2026-08-29), so every
route in this module is this fake's own, under the ``/__clover/`` prefix that
says so. A consumer's production code never calls these; their test fixtures
do, the way they would click through the dashboard.

=======================================  ==========================================
Register a callback (dashboard: add URL)  ``POST /__clover/webhooks/subscriptions``
Paste the verification code               ``POST /__clover/webhooks/verify``
See what is registered                    ``GET  /__clover/webhooks/subscriptions``
=======================================  ==========================================

THE DOCUMENTED HANDSHAKE, and how it maps onto the core's dispatcher:

1. The dashboard takes a callback URL. Here: ``POST .../subscriptions`` with
   ``{"url": ..., "eventKeys": [...]}`` inserts a subscription into the core's
   one subscription collection -- the same one the control plane and a seeded
   profile write -- marked ``verified: false`` and subscribed to **no event
   types**, so the dispatcher's fan-out never selects it.
2. Clover POSTs ``{"verificationCode": "<uuid>"}`` to the URL. Here: the same
   request enqueues that document to that one subscriber through
   :meth:`~vendorfake.core.webhooks.dispatcher.WebhookDispatcher.enqueue_to`,
   which is the real delivery path -- sink, retry schedule, delivery log --
   with the one difference the signer makes: no ``X-Clover-Auth`` yet, because
   the code is documented as sent only after validation. A consumer with no
   live endpoint reads the code off ``GET /__unit/webhooks/deliveries``, which
   is the fake's stand-in for their inbox.
3. The developer pastes the code into the dashboard. Here:
   ``POST .../verify`` with the code marks the subscription verified, sets its
   event types to the keys it asked for, and returns the auth code. From then
   on every matching mutation is delivered with ``X-Clover-Auth``.

A subscriber that arrives already carrying an auth code -- a profile's
``webhooks.subscribers`` entry, ``POST /__unit/webhooks/subscriptions``, a
seeded scenario -- is treated as verified: it has no ``verified`` key at all,
and only an explicit ``false`` means pending. That is the "pre-verified
subscription" the design brief allows, and it is how the quickstart avoids
the handshake while a test that wants it can still have it.

JUDGMENT, each labelled at its site
-----------------------------------
* **Per-key subscription.** The dashboard lets an app choose which event keys
  to receive; this unit models it as ``eventKeys`` and stores it as the core's
  ``<key>:*`` patterns, so the filter is the dispatcher's own matcher.
* **Ids.** Clover has no subscription ids; ``wbhk_<12 hex>`` from the
  internal id stream, deterministic per seed like every other id.
* **The auth code is minted at registration** and revealed at verification.
  Clover shows it in the dashboard once the URL is validated; minting early
  keeps the id stream's draw order independent of when the consumer verifies.
* **No delete, no re-send.** ``DELETE /__unit/webhooks/subscriptions/{id}``
  already exists on the control plane, and re-registering the same URL mints
  a fresh pending subscription; a second endpoint for either would be a
  second thing to keep in step with the core's list.

INVARIANT: **there is one subscription list, and the core owns it.** These
handlers read and write ``SUBSCRIPTION_COLLECTION`` and nothing else; the
journal entries they produce are the ones the dispatcher already ignores, so
registering a callback does not notify every subscriber that somebody
subscribed.

DOCUMENTED -- **HTTPS only.** "Clover supports only HTTPS-enabled callbacks"
/ "Set up a publicly accessible HTTPs endpoint" (the webhooks page). The
register route refuses any other scheme with ``invalid_value`` naming ``url``,
unless the vendor config's ``allow_insecure_callbacks`` lifts it for a local
receiver (JUDGMENT, labelled on ``CloverConfig``). A profile's
``webhooks.subscribers`` are not checked: they are the dashboard's
pre-verified entries, not a request to this route, and a scenario is entitled
to point them wherever its author's receiver listens.

KNOWN LIMITATION, tracked as konyklabs/roadmap#38 -- **nothing machine-readable
marks these routes as not-Clover.** The core's ``Route`` has one flag,
``internal``, and it means "control plane": no auth, no chaos, no capability
gate, and hidden from the vendor surface report. These routes want the gate
and the chaos and *also* want to be told apart from ``/oauth/v2/*`` in a
generated client, and ``Route`` has no field for that -- no tags, no
extensions. Until the core grows one, every summary below starts with
"Stand-in (not a Clover endpoint)" so that ``vendorfake openapi --vendor
clover`` and ``GET /__unit/routes`` at least say so in prose, and the
``/__clover/`` prefix says it in the path.
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
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION

__all__ = ["CAPABILITY", "PENDING_NAME", "CloverWebhooksSurface", "webhook_routes"]

CAPABILITY = "webhooks"
"""The capability every route below belongs to."""

PENDING_NAME = "dashboard stand-in subscription"
"""What a subscription registered through this surface is called in the
core's list, where ``name`` is a human-facing label and nothing more."""

STAND_IN = "Stand-in (not a Clover endpoint)"
"""The prefix every route summary carries. See the module docstring on
konyklabs/roadmap#38 for why it is prose rather than a flag."""

REQUIRED_SCHEME = "https"
"""The only callback scheme Clover accepts. Documented; see the module docstring."""

_VERIFIED = "verified"
_VERIFICATION_CODE = "verification_code"
_EVENT_KEYS = "event_keys"
"""Vendor-side fields on the core's subscription entity. The core's
``Subscription.from_entity`` ignores what it does not know, which is what lets
a vendor keep its own state on the shared record without a second list."""


class CloverWebhooksSurface:
    """The three stand-in routes, bound to one vendor's id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `auth`: the dashboard is the developer's own console, not a
        # merchant API, and the control plane these sit beside is open too.
        # No `example_body`: registering a callback commits a mutation the
        # dispatcher deliberately does not deliver, so a conformance check
        # driving "the example mutation" here would be measuring nothing.
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
        """Insert a pending subscription and send it its verification code.

        The subscription is ``enabled`` with **no** event types: the dispatcher
        skips a disabled subscriber even for a targeted send, and the
        verification POST is exactly a targeted send, so "not yet receiving
        events" is expressed as "subscribed to nothing" rather than as
        disabled. ``verify`` is what fills the event types in.

        JUDGMENT -- **refused with 503 while delivery is disabled.** A profile
        with ``webhooks.disable_delivery`` makes ``enqueue_to`` a silent no-op,
        so the code could never reach the callback, no delivery record would
        carry it, and ``verify`` could never succeed -- a 201 there would be a
        registration that can only ever hang. Clover has no equivalent state,
        so the refusal is this fake's; it names the alternative (a pre-verified
        subscriber through the control plane) rather than leaving the consumer
        to discover the hang.
        """
        ctx = args.ctx
        request = validate_body(RegisterSubscriptionRequest, args.body())
        self._require_https(request.url)
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
        """ "Clover supports only HTTPS-enabled callbacks." Read live from the
        config, so a profile's ``allow_insecure_callbacks`` is in force on the
        next request rather than the next process."""
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
        """Match the pasted code, activate the subscription, reveal the auth code.

        Idempotent on a verified subscription: the code stays on the record,
        so pasting it twice answers the same document twice rather than
        refusing a consumer whose first response was lost.

        A code matching nothing is a 400 ``invalid_value`` on ``verificationCode``
        and not a 404: the code is a field of the request being validated, not
        a resource being addressed -- the same treatment the OAuth surface
        gives an authorization code it will not accept.
        """
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
    """The keys a subscriber asked for.

    From its own ``event_keys`` when this surface registered it; otherwise
    derived from the core's ``event_types`` patterns, so a seeded or
    control-plane subscriber is reported in the same vocabulary: ``*`` is
    every key, and ``O:*`` or ``O:CREATE`` is ``O``.
    """
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
    """One stored subscription as the stand-in reports it. The auth code is
    the core's ``signature_key``, shown only once verified."""
    verified = entity.get(_VERIFIED) is not False
    return SubscriptionWire(
        id=str(entity["id"]),
        url=str(entity.get("notification_url", "")),
        eventKeys=list(_event_keys(entity)),
        verified=verified,
        authCode=str(entity.get("signature_key", "")) if verified else None,
    ).wire()
