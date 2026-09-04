"""An OpenAPI 3.1 document generated from the unit's own route table: a machine-readable
description of the surface this unit actually serves, without a second, hand-maintained copy.

Derived, never declared: every path, method, operation id, summary and path parameter comes
from the same ``RouteInfo`` rows the router was built from, so a route that exists is in the
document and one that does not cannot be. Byte-stable: paths, methods, tags and parameters are
emitted in a fixed order, so a diff between two runs is a real change, not dict ordering. Lives
in the core, not the transport adapter, since ``vendorfake openapi`` runs with no server.

The route table knows what is served, not what a body looks like: every operation declares a
permissive ``default`` response and no request schema, since that would need the vendor's own
payload models, a separate source that does not exist yet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from vendorfake.core.capability.registry import CONTROL_CAPABILITY
from vendorfake.core.kernel.unit import RouteInfo, Unit

__all__ = [
    "METHOD_ORDER",
    "OPENAPI_VERSION",
    "UNOFFICIAL_NOTICE",
    "document_for_unit",
    "openapi_document",
    "path_parameters",
]

OPENAPI_VERSION = "3.1.0"
"""3.1: the JSON-Schema-aligned version, with no separate ``nullable`` dialect."""

METHOD_ORDER: tuple[str, ...] = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
"""Emission order for the operations under one path: OpenAPI's own Path Item
Object field order, for a document a reader can diff by eye."""

_PARAM = re.compile(r"\{([A-Za-z0-9_]+)\}")
"""``{order_id}`` -- the one path-template syntax, shared with the router, the
chaos ``match.route`` key and the capability-to-routes index."""


def path_parameters(path: str) -> tuple[str, ...]:
    """The ``{name}`` segments of ``path``, in order, duplicates collapsed: OpenAPI keys parameters by (name, location)."""
    seen: list[str] = []
    for match in _PARAM.finditer(path):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def _parameter(name: str) -> dict[str, Any]:
    """One path parameter, always required and always a string, since the router hands every
    captured segment to the handler as one."""
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }


def _operation(route: RouteInfo) -> dict[str, Any]:
    """One Operation Object; ``x-unit-*`` is not decoration: ``capability`` predicts a 501, ``internal`` marks the control plane."""
    operation: dict[str, Any] = {}
    if route.operation_id is not None:
        operation["operationId"] = route.operation_id
    if route.summary is not None:
        operation["summary"] = route.summary
    operation["tags"] = [route.capability]
    parameters = [_parameter(name) for name in path_parameters(route.path)]
    if parameters:
        operation["parameters"] = parameters
    operation["responses"] = {
        "default": {
            "description": (
                "The unit's response. Status and body are decided by the vendor's error shaper "
                "and by this unit's profile, capability state and chaos rules."
            )
        }
    }
    operation["x-unit-capability"] = route.capability
    operation["x-unit-internal"] = route.internal
    operation["x-unit-serialized"] = route.serialized
    if route.auth is not None:
        operation["x-unit-auth"] = route.auth
    return operation


def _tag(name: str) -> dict[str, Any]:
    if name == CONTROL_CAPABILITY:
        return {"name": name, "description": "The unit's control plane. Always on; never part of the vendor surface."}
    return {"name": name, "description": f"Routes belonging to the {name} capability."}


def openapi_document(
    routes: Iterable[RouteInfo],
    *,
    title: str,
    version: str,
    description: str | None = None,
    include_internal: bool = True,
) -> dict[str, Any]:
    """Build the document for one route table; ``routes`` is the published :class:`RouteInfo` row, what ``GET /__unit/routes`` serves."""
    selected: list[RouteInfo] = [row for row in routes if include_internal or not row.internal]

    paths: dict[str, dict[str, Any]] = {}
    for row in sorted(selected, key=lambda r: (r.path, r.method)):
        item = paths.setdefault(row.path, {})
        method = row.method.lower()
        if method in item:
            # Same method and path twice: the router uses the first and the
            # second is dead, so keep only the first here too.
            continue
        item[method] = _operation(row)

    ordered_paths: dict[str, Any] = {}
    for path in sorted(paths):
        item = paths[path]
        ordered_paths[path] = {method: item[method] for method in METHOD_ORDER if method in item}

    tags: Sequence[str] = sorted({row.capability for row in selected})

    info: dict[str, Any] = {"title": title, "version": version}
    if description is not None:
        info["description"] = description

    return {
        "openapi": OPENAPI_VERSION,
        "info": info,
        "tags": [_tag(name) for name in tags],
        "paths": ordered_paths,
    }


UNOFFICIAL_NOTICE = (
    "Generated from this unit's route table. Unofficial: a fake, not the vendor's API, "
    "and not affiliated with or endorsed by them."
)
"""Carried in ``info.description``: this document often ends up detached from the README."""


def document_for_unit(unit: Unit, *, include_internal: bool = True) -> dict[str, Any]:
    """The document for a running unit: the one place its title is decided, so
    the transport adapter and ``vendorfake openapi`` cannot disagree on it.
    """
    vendor = unit.context.vendor
    return openapi_document(
        unit.control.list_routes(),
        title=f"{vendor.display_name} (vendorfake)",
        version=vendor.api_version or "unversioned",
        description=UNOFFICIAL_NOTICE,
        include_internal=include_internal,
    )
