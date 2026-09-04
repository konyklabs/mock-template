"""Webhooks tag: all five documented operations -- a real vendor surface (unlike
Toast's or Clover's), so nothing here is a stand-in.

DOCUMENTED: list/create(201, 409 on dup)/get(404)/update(404)/delete(200, 404), all under
the single unqualified ``webhooks`` scope. Bodies are the specification's own:
``WebhookRequest`` is ``{active, type, url}``, all required; ``Webhook`` adds ``id`` and
``retailer_id``. The list answers ``{"data": [...]}`` with no ``version`` member -- the
one list in this package that neither paginates nor carries the version envelope.

The 409 and the three 404s use the specification's own one-member ``{"error": <string>}``
shape (``errors.py``'s ``ONE_MEMBER_BODY_INFO_KEY``); the 409's uniqueness key is the
``(type, url)`` pair. Delete answers an empty 200 (documented with no content), not 204.

INVARIANT: there is one subscription list, and the core owns it -- these handlers read
and write ``SUBSCRIPTION_COLLECTION``, the same list a profile's ``webhooks.subscribers``
and the control plane's subscription route populate. A Lightspeed webhook carries exactly
one ``type``, so ``event_types`` is always a one-element list.

``WebhookRequest`` carries no secret: Lightspeed signs with the application's
``client_secret``, so every subscription created here carries that as its
``signature_key`` (see ``signer.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitContext, UnitError, UnitErrorKind
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION
from vendorfake.lightspeed.config import SCOPE_WEBHOOKS
from vendorfake.lightspeed.errors import ONE_MEMBER_BODY_INFO_KEY, WEBHOOK_DUPLICATE_MESSAGE
from vendorfake.lightspeed.events import LIGHTSPEED_EVENT_TYPES
from vendorfake.lightspeed.model.common import validate_body
from vendorfake.lightspeed.model.webhooks import WebhookRequest, project_webhook
from vendorfake.lightspeed.paths import (
    CREATE_WEBHOOK,
    DELETE_WEBHOOK,
    GET_WEBHOOK,
    LIST_WEBHOOKS,
    UPDATE_WEBHOOK,
)
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, require_retailer

__all__ = ["CAPABILITY", "REQUIRED_SCHEME", "LightspeedWebhooksSurface", "webhook_routes"]

CAPABILITY = "webhooks"

REQUIRED_SCHEME = "https"
"""Required scheme when ``allow_insecure_callbacks`` is off; the spec names none, so the
default is permissive (see ``config.py``)."""


class LightspeedWebhooksSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        # No `example_body`: a webhook subscription is a mutation the dispatcher
        # never delivers to itself; `surface/sales.py`'s create is the example route.
        return (
            Route(
                method="GET",
                path=LIST_WEBHOOKS,
                capability=CAPABILITY,
                handler=self.list_webhooks,
                auth=BEARER_AUTH,
                scopes=(SCOPE_WEBHOOKS,),
                operation_id="ListWebhooks",
                summary='All webhooks: {"data": [...]}, with no version envelope. Vendor operationId: get-webhooks.',
            ),
            Route(
                method="POST",
                path=CREATE_WEBHOOK,
                capability=CAPABILITY,
                handler=self.create_webhook,
                auth=BEARER_AUTH,
                scopes=(SCOPE_WEBHOOKS,),
                operation_id="CreateWebhook",
                summary="Create a webhook; 201, or 409 when the type and URL pair already exists. Vendor: post-webhooks.",
            ),
            Route(
                method="GET",
                path=GET_WEBHOOK,
                capability=CAPABILITY,
                handler=self.get_webhook,
                auth=BEARER_AUTH,
                scopes=(SCOPE_WEBHOOKS,),
                operation_id="GetWebhook",
                summary="One webhook by id; 404 otherwise. Vendor operationId: get-webhooks-id.",
            ),
            Route(
                method="PUT",
                path=UPDATE_WEBHOOK,
                capability=CAPABILITY,
                handler=self.update_webhook,
                auth=BEARER_AUTH,
                scopes=(SCOPE_WEBHOOKS,),
                operation_id="UpdateWebhook",
                summary="Replace a webhook's active, type and url; 404 otherwise. Vendor: put-webhooks-id.",
            ),
            Route(
                method="DELETE",
                path=DELETE_WEBHOOK,
                capability=CAPABILITY,
                handler=self.delete_webhook,
                auth=BEARER_AUTH,
                scopes=(SCOPE_WEBHOOKS,),
                operation_id="DeleteWebhook",
                summary="Delete a webhook; empty 200, or 404. Vendor: delete-webhooks-webhookId.",
            ),
        )

    # -- reads --------------------------------------------------------------

    def list_webhooks(self, args: HandlerArgs) -> ReplyInit:
        retailer_id = str(require_retailer(args.ctx)["id"])
        rows = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).all()
        return json_({"data": [project_webhook(row, retailer_id=retailer_id) for row in rows]})

    def get_webhook(self, args: HandlerArgs) -> ReplyInit:
        stored = self._require(args)
        retailer_id = str(require_retailer(args.ctx)["id"])
        return json_({"data": project_webhook(stored, retailer_id=retailer_id)})

    # -- writes -------------------------------------------------------------

    def create_webhook(self, args: HandlerArgs) -> ReplyInit:
        request = self._validate(args)
        self._refuse_duplicate(args.ctx, request, exclude=None)
        retailer_id = str(require_retailer(args.ctx)["id"])
        entity = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).insert(
            {
                "id": self._deps.ids.webhook(),
                "name": f"{request.type} -> {request.url}",
                "notification_url": request.url,
                "event_types": [request.type],
                "signature_key": self._deps.config.client_secret,
                "enabled": request.active,
            },
            {"operation_id": "CreateWebhook"},
        )
        return json_({"data": project_webhook(entity, retailer_id=retailer_id)}, 201)

    def update_webhook(self, args: HandlerArgs) -> ReplyInit:
        # Body first, then the 404. See `surface/registers.py::open_register`.
        request = self._validate(args)
        stored = self._require(args)
        self._refuse_duplicate(args.ctx, request, exclude=str(stored["id"]))
        retailer_id = str(require_retailer(args.ctx)["id"])

        def mutate(draft: dict[str, Any]) -> None:
            draft["notification_url"] = request.url
            draft["event_types"] = [request.type]
            draft["enabled"] = request.active
            draft["name"] = f"{request.type} -> {request.url}"

        updated = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).update(
            str(stored["id"]), mutate, meta={"operation_id": "UpdateWebhook"}
        )
        return json_({"data": project_webhook(updated, retailer_id=retailer_id)})

    def delete_webhook(self, args: HandlerArgs) -> ReplyInit:
        stored = self._require(args)
        args.ctx.store.collection(SUBSCRIPTION_COLLECTION).delete(str(stored["id"]), {"operation_id": "DeleteWebhook"})
        # 200 with no body, per the specification's `{"description": "OK"}`.
        return ReplyInit(status=200, text="")

    # -- helpers ------------------------------------------------------------

    def _validate(self, args: HandlerArgs) -> WebhookRequest:
        request = validate_body(WebhookRequest, args.body())
        if request.type not in LIGHTSPEED_EVENT_TYPES:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"type must be one of: {', '.join(LIGHTSPEED_EVENT_TYPES)}.",
                field="type",
                info={"supplied": request.type},
            )
        scheme = urlsplit(request.url).scheme.lower()
        if scheme != REQUIRED_SCHEME and not self._deps.config.allow_insecure_callbacks:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"url must be an {REQUIRED_SCHEME}:// endpoint. Lightspeed's WebhookRequest names no "
                    f"scheme, so this unit accepts any by default; allow_insecure_callbacks is off on this "
                    f"profile. Got scheme {scheme or '(none)'!r}."
                ),
                field="url",
            )
        return request

    def _refuse_duplicate(self, ctx: UnitContext, request: WebhookRequest, *, exclude: str | None) -> None:
        for row in ctx.store.collection(SUBSCRIPTION_COLLECTION).all():
            if exclude is not None and str(row["id"]) == exclude:
                continue
            if _matches(row, request):
                raise UnitError(
                    UnitErrorKind.CONFLICT,
                    detail=WEBHOOK_DUPLICATE_MESSAGE,
                    info={ONE_MEMBER_BODY_INFO_KEY: True, "type": request.type, "url": request.url},
                )

    def _require(self, args: HandlerArgs) -> dict[str, Any]:
        webhook_id = args.params["webhookId"]
        stored = args.ctx.store.collection(SUBSCRIPTION_COLLECTION).get(webhook_id)
        if stored is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Webhook {webhook_id} was not found.",
                field="webhookId",
                info={ONE_MEMBER_BODY_INFO_KEY: True},
            )
        return stored


def _matches(entity: Mapping[str, Any], request: WebhookRequest) -> bool:
    """Whether ``entity`` already occupies this ``(type, url)`` pair."""
    types = entity.get("event_types")
    return entity.get("notification_url") == request.url and isinstance(types, list) and request.type in types


def webhook_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedWebhooksSurface(deps).routes()
