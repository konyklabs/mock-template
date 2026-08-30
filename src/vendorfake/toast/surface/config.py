"""The configuration API surface: thirteen resources, list and by guid.

FOR: the reference data an ordering integration reads once to build its
pickers -- dining options, the alternate payment types an OTHER payment names,
tax rates, revenue centres, service areas, tables, restaurant services,
discounts, service charges, the config-flavoured menu entities, void reasons.

DOCUMENTED (toast-config-api.yaml v2.5.0; ``model/config.py`` lists the
shapes): ``GET /config/v2/<resource>`` and ``GET /config/v2/<resource>/{guid}``;
``lastModified`` (e.g. ``2024-06-20T00:00:00.000+0000``) returns entities
modified at or after that instant (JUDGMENT on inclusivity -- the page gives
the parameter and not the comparison); ``pageToken`` continues a list and the
``Toast-Next-Page-Token`` response header carries the next one, omitted on
the last page; at most 300 items per page. No ``page``/``pageSize``.

JUDGMENT: the page token is the core's opaque cursor -- fingerprinted to the
resource and the ``lastModified`` it was issued for, so a token replayed
against another list is a 400 rather than the wrong page -- because Toast
documents the header and not the token's format. Every route in this module
requires ``Toast-Restaurant-External-ID``; the lists are the restaurant's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.toast.model.config import (
    CONFIG_RESOURCES,
    MAX_PAGE,
    MODIFIED_KEY,
    ConfigResource,
    project_config_entity,
)
from vendorfake.toast.model.dates import parse_rest_date
from vendorfake.toast.surface.common import RESTAURANT_AUTH, ToastDeps, require_restaurant

__all__ = ["CAPABILITY", "NEXT_PAGE_TOKEN_HEADER", "ToastConfigSurface", "config_routes"]

CAPABILITY = "config"

NEXT_PAGE_TOKEN_HEADER = "Toast-Next-Page-Token"
"""Documented, in the documented casing."""


class ToastConfigSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        routes: list[Route] = []
        for resource in CONFIG_RESOURCES:
            routes.append(
                Route(
                    method="GET",
                    path=f"/config/v2/{resource.segment}",
                    capability=CAPABILITY,
                    handler=self._lister(resource),
                    auth=RESTAURANT_AUTH,
                    scopes=("config:read",),
                    operation_id=f"Config{resource.entity_type}sGet",
                    summary=f"Every {resource.entity_type}; lastModified filter; Toast-Next-Page-Token paging.",
                )
            )
            routes.append(
                Route(
                    method="GET",
                    path=f"/config/v2/{resource.segment}/{{guid}}",
                    capability=CAPABILITY,
                    handler=self._getter(resource),
                    auth=RESTAURANT_AUTH,
                    scopes=("config:read",),
                    operation_id=f"Config{resource.entity_type}Get",
                    summary=f"One {resource.entity_type} by guid.",
                )
            )
        return tuple(routes)

    def _lister(self, resource: ConfigResource) -> Any:
        def list_resource(args: HandlerArgs) -> ReplyInit:
            require_restaurant(args)
            since = _last_modified(args)
            rows = [
                row
                for row in args.ctx.store.collection(resource.collection).all()
                if since is None or int(row.get(MODIFIED_KEY, 0)) >= since
            ]
            page = args.ctx.store.collection(resource.collection).paginate(
                rows,
                limit=MAX_PAGE,
                cursor=args.query("pageToken"),
                fingerprint={"resource": resource.segment, "lastModified": since},
                max_limit=MAX_PAGE,
                default_limit=MAX_PAGE,
            )
            headers = {} if page.cursor is None else {NEXT_PAGE_TOKEN_HEADER: page.cursor}
            return json_([project_config_entity(resource, row) for row in page.items], headers=headers)

        return list_resource

    def _getter(self, resource: ConfigResource) -> Any:
        def get_resource(args: HandlerArgs) -> ReplyInit:
            require_restaurant(args)
            guid = args.params["guid"]
            stored = args.ctx.store.collection(resource.collection).get(guid)
            if stored is None:
                raise UnitError(
                    UnitErrorKind.NOT_FOUND,
                    detail=f"{resource.entity_type} {guid} was not found.",
                    field="guid",
                )
            return json_(project_config_entity(resource, stored))

        return get_resource


def _last_modified(args: HandlerArgs) -> int | None:
    raw = args.query("lastModified")
    if raw is None:
        return None
    return parse_rest_date(raw, field="lastModified")


def config_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastConfigSurface(deps).routes()


def config_entity(args: HandlerArgs, resource: ConfigResource, guid: str) -> Mapping[str, Any] | None:
    """A stored config document by guid, for the surfaces that reference one."""
    return args.ctx.store.collection(resource.collection).get(guid)
