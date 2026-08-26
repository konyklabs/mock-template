"""The OpenAPI document generated from a route table.

Every assertion here is about the document being *derived*: the paths come from
the table, the order comes from a rule rather than from dict insertion, and
nothing is described that the table does not know.
"""

from __future__ import annotations

import json

from vendorfake.core.capability.registry import CONTROL_CAPABILITY
from vendorfake.core.control.openapi import METHOD_ORDER, OPENAPI_VERSION, openapi_document, path_parameters
from vendorfake.core.kernel.unit import RouteInfo
from vendorfake.core.util.json import dump_json


def row(
    method: str,
    path: str,
    *,
    capability: str = "orders",
    internal: bool = False,
    operation_id: str | None = None,
    summary: str | None = None,
    auth: str | None = None,
) -> RouteInfo:
    return RouteInfo(
        method=method,
        path=path,
        capability=capability,
        auth=auth,
        operation_id=operation_id,
        summary=summary,
        internal=internal,
    )


def build(*rows: RouteInfo, include_internal: bool = True) -> dict[str, object]:
    return openapi_document(rows, title="T", version="v1", include_internal=include_internal)


def test_path_parameters_reads_brace_templates() -> None:
    """``{order_id}``, the one template syntax.

    The reference carried a colon form and translated it to braces wherever
    OpenAPI was involved; making braces canonical deletes the translation, so
    there is exactly one pattern to match here and no second form to keep in
    step with it.
    """
    assert path_parameters("/v2/orders/{order_id}/line-items/{uid}") == ("order_id", "uid")
    assert path_parameters("/v2/orders") == ()


def test_repeated_parameter_names_collapse() -> None:
    """OpenAPI keys parameters by (name, location), so a duplicate is invalid.

    The router's own parameter dict has the same collision, so collapsing here
    cannot hide a difference that the router would have honoured.
    """
    assert path_parameters("/a/{id}/b/{id}") == ("id",)


def test_the_document_declares_its_version_and_info() -> None:
    document = build(row("GET", "/v2/orders"))
    assert document["openapi"] == OPENAPI_VERSION == "3.1.0"
    assert document["info"] == {"title": "T", "version": "v1"}


def test_every_route_appears_and_only_routes_appear() -> None:
    """A route that exists is described; nothing else is.

    This is the property a framework's own generator cannot have here: pointed
    at one catch-all route it would publish a single wildcard entry.
    """
    document = build(
        row("GET", "/v2/orders", operation_id="ListOrders"),
        row("POST", "/v2/orders", operation_id="CreateOrder"),
        row("GET", "/v2/orders/{order_id}", operation_id="RetrieveOrder"),
    )
    paths = document["paths"]
    assert isinstance(paths, dict)
    assert set(paths) == {"/v2/orders", "/v2/orders/{order_id}"}
    assert set(paths["/v2/orders"]) == {"get", "post"}
    assert paths["/v2/orders"]["post"]["operationId"] == "CreateOrder"


def test_path_parameters_are_declared_required_strings() -> None:
    """Required because OpenAPI insists; strings because the router hands the
    handler a string and declaring an integer would describe a coercion that
    does not happen."""
    document = build(row("GET", "/v2/orders/{order_id}"))
    parameters = document["paths"]["/v2/orders/{order_id}"]["get"]["parameters"]  # type: ignore[index]
    assert parameters == [{"name": "order_id", "in": "path", "required": True, "schema": {"type": "string"}}]


def test_a_route_without_parameters_declares_none() -> None:
    """An empty ``parameters`` list is noise; omitting the key says the same
    thing and keeps the document smaller than the route table."""
    document = build(row("GET", "/v2/orders"))
    assert "parameters" not in document["paths"]["/v2/orders"]["get"]  # type: ignore[index]


def test_capability_and_internal_are_carried_as_extensions() -> None:
    """The two facts a client generator most needs and OpenAPI has no field for.

    ``capability`` predicts which routes a profile with that capability off
    will answer 501 on; ``internal`` separates the unit's own control plane
    from the vendor surface it is faking.
    """
    document = build(row("GET", "/__unit/health", capability=CONTROL_CAPABILITY, internal=True))
    operation = document["paths"]["/__unit/health"]["get"]  # type: ignore[index]
    assert operation["x-unit-capability"] == CONTROL_CAPABILITY
    assert operation["x-unit-internal"] is True
    assert operation["tags"] == [CONTROL_CAPABILITY]


def test_auth_is_carried_only_when_the_route_declares_it() -> None:
    with_auth = build(row("GET", "/v2/orders", auth="bearer"))
    without = build(row("GET", "/v2/orders"))
    assert with_auth["paths"]["/v2/orders"]["get"]["x-unit-auth"] == "bearer"  # type: ignore[index]
    assert "x-unit-auth" not in without["paths"]["/v2/orders"]["get"]  # type: ignore[index]


def test_internal_routes_can_be_dropped() -> None:
    document = build(
        row("GET", "/v2/orders"),
        row("GET", "/__unit/health", capability=CONTROL_CAPABILITY, internal=True),
        include_internal=False,
    )
    assert set(document["paths"]) == {"/v2/orders"}  # type: ignore[arg-type]
    assert [tag["name"] for tag in document["tags"]] == ["orders"]  # type: ignore[index,union-attr]


def test_output_is_byte_stable_regardless_of_input_order() -> None:
    """Two route tables with the same rows produce the same bytes.

    Without this the document is not diffable: a change of registration order
    would look like a change of surface, and the two would be indistinguishable
    in review.
    """
    rows = [
        row("POST", "/v2/orders"),
        row("GET", "/v2/orders/{order_id}"),
        row("GET", "/v2/orders"),
        row("DELETE", "/v2/orders/{order_id}"),
    ]
    first = dump_json(openapi_document(rows, title="T", version="v1"))
    second = dump_json(openapi_document(list(reversed(rows)), title="T", version="v1"))
    assert first == second


def test_methods_under_one_path_follow_the_specification_s_order() -> None:
    """Any fixed order would be stable; this one is also familiar to a reader
    diffing the document by eye."""
    rows = [row(method.upper(), "/v2/orders") for method in ("patch", "get", "delete", "post")]
    document = openapi_document(rows, title="T", version="v1")
    emitted = list(document["paths"]["/v2/orders"])  # type: ignore[index,arg-type]
    assert emitted == [method for method in METHOD_ORDER if method in set(emitted)]
    assert emitted == ["get", "post", "delete", "patch"]


def test_a_duplicate_method_on_one_path_keeps_the_first() -> None:
    """Which is what the router does: it returns the first registered route and
    the second is dead. Reporting both would produce an invalid document and
    hide the defect."""
    document = build(
        row("GET", "/v2/orders", operation_id="First"),
        row("GET", "/v2/orders", operation_id="Second"),
    )
    assert document["paths"]["/v2/orders"]["get"]["operationId"] == "First"  # type: ignore[index]


def test_the_document_is_json_serialisable_by_the_wire_encoder() -> None:
    """It goes out through the same ``dump_json`` every response uses, so
    anything it cannot encode is a defect here rather than a surprise at the
    edge."""
    document = build(row("GET", "/v2/orders/{order_id}", summary="Retrieve an order."))
    assert json.loads(dump_json(document)) == document
