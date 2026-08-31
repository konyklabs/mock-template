"""The subscription stand-in: what the Toast developer portal does, as routes.

TOAST HAS NO SUBSCRIPTION API. Two official pages describe how a subscription
comes to exist -- "you work with the Toast Integrations team" on
apiSubscriptions.html, "View and manage your webhooks" in the developer
portal on apiDeveloperPortal.html -- and neither is an endpoint (audit gap
10). Every route in this module is this fake's own, under the ``/__toast/``
prefix that says so; a consumer's production code never calls these, their
test fixtures do.

=====================================  ===============================================
Add a subscription (portal: add URL)   ``POST   /__toast/webhooks/subscriptions``
See what is registered                 ``GET    /__toast/webhooks/subscriptions``
Remove one                             ``DELETE /__toast/webhooks/subscriptions/{guid}``
=====================================  ===============================================

DOCUMENTED, and enforced: the endpoint "must be HTTPS" with TLS 1.2 or later
(apiEndpointRequirements.html) -- any other scheme is refused unless the
vendor config's ``allow_insecure_callbacks`` lifts it for a local receiver
(JUDGMENT, labelled on ``ToastConfig``); a subscription has a secret of its
own ("the webhook secret", generated per subscription per environment,
apiMessageSigning.html) -- minted here from the id stream when the caller
supplies none. There is no verification handshake: Toast documents none.

INVARIANT: **there is one subscription list, and the core owns it.** These
handlers read and write ``SUBSCRIPTION_COLLECTION`` and nothing else; a
subscription's categories are stored as the core's ``event_types`` -- the
documented type names of each category -- so the filter is the dispatcher's
own matcher. A profile's ``webhooks.subscribers`` and the control plane's
``POST /__unit/webhooks/subscriptions`` land in the same list.

KNOWN LIMITATION, tracked as konyklabs/roadmap#38: nothing machine-readable
marks these routes as not-Toast; every summary starts with the stand-in label.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from vendorfake.core.kernel.reply import json_, no_content
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION
from vendorfake.toast.model.common import validate_body
from vendorfake.toast.model.webhooks import (
    ALL_CATEGORIES,
    CATEGORY_TYPES,
    RegisterSubscriptionRequest,
    SubscriptionWire,
)
from vendorfake.toast.surface.common import ToastDeps

__all__ = ["CAPABILITY", "STAND_IN", "ToastWebhooksSurface", "webhook_routes"]

CAPABILITY = "webhooks"

STAND_IN = "Stand-in (not a Toast endpoint)"
REQUIRED_SCHEME = "https"

_CATEGORIES = "event_categories"
"""Vendor-side field on the core's subscription entity, so the categories a
caller named are reported back in their own vocabulary."""


class ToastWebhooksSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `auth`: the portal is the developer's own console. No
        # `example_body`: registering a subscriber is a mutation the
        # dispatcher deliberately does not deliver.
        return (
            Route(
                method="POST",
                path="/__toast/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.register,
                operation_id="RegisterWebhookSubscription",
                summary=f"{STAND_IN}: add a subscription -- an HTTPS URL, categories, and a per-subscription secret.",
            ),
            Route(
                method="GET",
                path="/__toast/webhooks/subscriptions",
                capability=CAPABILITY,
                handler=self.list_subscriptions,
                operation_id="ListWebhookSubscriptions",
                summary=f"{STAND_IN}: every subscription, with its secret.",
            ),
            Route(
                method="DELETE",
                path="/__toast/webhooks/subscriptions/{guid}",
                capability=CAPABILITY,
                handler=self.remove,
                operation_id="RemoveWebhookSubscription",
                summary=f"{STAND_IN}: remove a subscription (Toast: 'contact the Integrations team').",
            ),
        )

    def register(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        request = validate_body(RegisterSubscriptionRequest, args.body())
        self._require_https(request.url)
        ids = self._deps.ids
        guid = ids.internal("sub")
        secret = request.secret if request.secret is not None else ids.guid()
        entity = ctx.store.collection(SUBSCRIPTION_COLLECTION).insert(
            {
                "id": guid,
                "name": "portal stand-in subscription",
                "notification_url": request.url,
                "event_types": [t for c in request.eventCategories for t in CATEGORY_TYPES[c]],
                "signature_key": secret,
                "enabled": True,
                _CATEGORIES: list(request.eventCategories),
            },
            {"operation_id": "RegisterWebhookSubscription"},
        )
        return json_(_project(entity), 201)

    def _require_https(self, url: str) -> None:
        scheme = urlsplit(url).scheme.lower()
        if scheme == REQUIRED_SCHEME or self._deps.config.allow_insecure_callbacks:
            return
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"url must be an https:// endpoint: Toast requires HTTPS with TLS 1.2 or later "
                f"(https://doc.toasttab.com/doc/devguide/apiEndpointRequirements.html). Got scheme {scheme or '(none)'!r}; "
                "set the vendor config's allow_insecure_callbacks to register a local http:// receiver against this fake."
            ),
            field="url",
        )

    def list_subscriptions(self, args: HandlerArgs) -> ReplyInit:
        rows = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).all()
        return json_({"subscriptions": [_project(entity) for entity in rows]})

    def remove(self, args: HandlerArgs) -> ReplyInit:
        guid = args.params["guid"]
        removed = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).delete(
            guid, {"operation_id": "RemoveWebhookSubscription"}
        )
        if not removed:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Subscription {guid} was not found.", field="guid")
        return no_content()


def webhook_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastWebhooksSurface(deps).routes()


def _categories(entity: Mapping[str, Any]) -> list[str]:
    stored = entity.get(_CATEGORIES)
    if isinstance(stored, list):
        return [str(c) for c in stored]
    patterns = entity.get("event_types", ())
    if not isinstance(patterns, list):
        return []
    if "*" in patterns:
        return list(ALL_CATEGORIES)
    return [c for c, types in CATEGORY_TYPES.items() if any(t in patterns for t in types)]


def _project(entity: Mapping[str, Any]) -> dict[str, Any]:
    return SubscriptionWire(
        guid=str(entity["id"]),
        url=str(entity.get("notification_url", "")),
        eventCategories=_categories(entity),
        secret=str(entity.get("signature_key", "")),
        enabled=entity.get("enabled", True) is not False,
    ).wire()
