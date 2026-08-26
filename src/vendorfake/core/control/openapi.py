"""An OpenAPI 3.1 document generated from the unit's own route table.

FOR: handing a consumer's tooling a machine-readable description of the
surface this unit actually serves -- the paths the router dispatches on, the
capability each one belongs to, the operation ids the spec-freshness inventory
uses -- without a second, hand-maintained copy of any of it.

INVARIANT: **the document is derived, never declared.** Every path, method,
operation id, summary and path parameter here comes from the same
``RouteInfo`` rows the router was built from, so a route that exists is in the
document and a route that does not cannot be. The alternative -- a web
framework's own generator -- cannot see any of this: the transport adapter
registers exactly one catch-all route, on purpose, so that the framework can
never answer a 404, a 405 or a 422 of its own, and a generator pointed at that
route would publish a single wildcard entry. Deriving from the route table is
what buys back the description the catch-all costs, and it produces a *richer*
document than the framework's, because capability and operation id are facts
the framework was never told.

SECOND INVARIANT: **byte-stable output.** Paths, methods, tags and parameters
are emitted in a fixed order, so two runs of the same unit produce the same
document and a diff between two versions is a real change rather than dict
ordering. Callers serialise it with the same ``dump_json`` every response goes
through.

This module lives in the core, not in the transport adapter, for one concrete
reason: ``vendorfake openapi`` prints the document with no server running and
must not pay for -- or be able to reach -- a web framework to do it.

Scope, stated plainly so nobody reads more into the document than it carries:
the route table knows *what is served*, not *what a body looks like*. So every
operation declares a permissive ``default`` response and no request schema.
Request and response schemas would have to come from the vendor's payload
models, which are a separate source; until they do, an empty schema is an
honest statement of ignorance and a wrong one would be worse than none.
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
"""The document's own version. 3.1 because it is the JSON-Schema-aligned one,
and because nothing here needs 3.0's separate ``nullable`` dialect."""

METHOD_ORDER: tuple[str, ...] = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
"""Emission order for the operations under one path.

The order OpenAPI's own Path Item Object lists its fields in. Any fixed order
would make the output stable; using the specification's makes it also familiar
to a reader diffing the document by eye."""

_PARAM = re.compile(r"\{([A-Za-z0-9_]+)\}")
"""``{order_id}`` -- the one path-template syntax, shared with the router, the
chaos ``match.route`` key and the capability-to-routes index. There is no
second form to translate from, which is the whole point of making braces
canonical."""


def path_parameters(path: str) -> tuple[str, ...]:
    """The ``{name}`` segments of ``path``, in the order they appear.

    Duplicates are collapsed: OpenAPI keys a path item's parameters by
    (name, location), so the same name twice would be an invalid document
    rather than two parameters. The router would not have built such a route
    either -- its parameter dict has the same key collision -- so this cannot
    hide a real difference.
    """
    seen: list[str] = []
    for match in _PARAM.finditer(path):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def _parameter(name: str) -> dict[str, Any]:
    """One path parameter, always required and always a string.

    Required because OpenAPI says a path parameter must be; a string because
    the router hands every captured segment to the handler as one, so
    declaring an integer here would describe a coercion that does not happen.
    """
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }


def _operation(route: RouteInfo) -> dict[str, Any]:
    """One Operation Object, with what the route table actually knows.

    The ``x-unit-*`` extensions are not decoration. ``capability`` is what a
    consumer needs in order to predict which routes a profile with that
    capability switched off will answer 501 on, and it exists in no other
    machine-readable form; ``internal`` separates the unit's own control plane
    from the vendor surface it is faking, which is the single most important
    distinction in the document for anyone generating a client from it.
    """
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
    """Build the document for one route table.

    ``routes`` is deliberately the published :class:`RouteInfo` row rather than
    the :class:`~vendorfake.core.kernel.types.Route` it came from: the row is
    exactly what ``GET /__unit/routes`` already serves, so a consumer holding
    that JSON has everything this function needs and could generate the same
    document from outside the process. That is the property that keeps the
    document a statement about the *unit* rather than about this codebase.

    ``include_internal=False`` drops the ``/__unit/*`` control plane, for the
    case where the document is meant to describe only what the fake is
    pretending to be.
    """
    selected: list[RouteInfo] = [row for row in routes if include_internal or not row.internal]

    paths: dict[str, dict[str, Any]] = {}
    for row in sorted(selected, key=lambda r: (r.path, r.method)):
        item = paths.setdefault(row.path, {})
        method = row.method.lower()
        if method in item:
            # Two routes with the same method and path: the router returns the
            # first registered and the second is dead. Reporting both would
            # produce an invalid document and hide the defect; keeping the
            # first mirrors what the router does.
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
"""Carried in ``info.description`` of every generated document.

A machine-readable document is the artifact most likely to end up detached from
the README that says what this project is -- pasted into a client generator, or
committed to a consumer's repository -- so the disclaimer travels with it.
"""


def document_for_unit(unit: Unit, *, include_internal: bool = True) -> dict[str, Any]:
    """The document for a running unit: the one place its title is decided.

    Two callers need this and they must not disagree: the transport adapter
    serves the bytes at ``/__unit/openapi.json``, and ``vendorfake openapi``
    prints them with no server running. Deriving the title and version here
    rather than at each call site is what makes "the same document, two
    renderings" a fact rather than a convention -- a second copy of the naming
    would drift the first time either changed.
    """
    vendor = unit.context.vendor
    return openapi_document(
        unit.control.list_routes(),
        title=f"{vendor.display_name} (vendorfake)",
        version=vendor.api_version or "unversioned",
        description=UNOFFICIAL_NOTICE,
        include_internal=include_internal,
    )
