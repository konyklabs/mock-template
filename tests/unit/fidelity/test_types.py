"""The shared reading of ``declaration.json`` and ``extract.json``.

Every other fidelity test builds on ``Surface.classify``; this file pins what
the four kinds mean, and that the loaders refuse the documents they should.
"""

from __future__ import annotations

from typing import Any

import pytest

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import ReplyInit, Route
from vendorfake.fidelity.types import (
    Alias,
    Excuse,
    Extract,
    FidelityDeclaration,
    Operation,
    SpecSource,
    Surface,
    load_declaration,
    load_extract,
    route_key,
    template_shape,
)


def _handler(_: Any) -> ReplyInit:
    return json_({})


def _route(method: str, path: str, *, internal: bool = False) -> Route:
    return Route(method=method, path=path, capability="core", handler=_handler, internal=internal)


def _json(ref: str) -> dict[str, Any]:
    return {"description": "x", "content": {"application/json": {"schema": {"$ref": ref}}}}


EXTRACT = Extract(
    {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/v1/things/{thing_id}": {
                "get": {"operationId": "GetThing", "responses": {"200": _json("#/components/schemas/Thing")}},
                "delete": {"operationId": "DeleteThing", "responses": {"default": _json("#/components/schemas/Err")}},
            },
            "/v1/things": {
                "post": {
                    "operationId": "CreateThing",
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}}
                    },
                    "responses": {"200": _json("#/components/schemas/Thing"), "404": {"description": "no body"}},
                }
            },
        },
        "components": {"schemas": {"Thing": {"type": "object"}, "Err": {"type": "object"}}},
        "x-vendorfake": {"schema": 1, "stubbed": []},
    }
)

DECLARATION = FidelityDeclaration(
    anchor="tests.synthetic",
    sources=(SpecSource(kind="openapi3", url="https://example.invalid/x.json"),),
    aliases=(Alias(method="GET", path="/v1/things/me", spec_path="/v1/things/{thing_id}", reason="documented"),),
    excused=(Excuse(method="GET", path="/v1/legacy", reason="kept"),),
    error_envelope="200",
)
SURFACE = Surface(DECLARATION, EXTRACT)


def test_template_shape_erases_parameter_names_only() -> None:
    assert template_shape("/v1/things/{thing_id}") == template_shape("/v1/things/{id}") == "/v1/things/{}"
    assert template_shape("/v1/things/me") == "/v1/things/me"
    assert route_key("get", "/x") == "GET /x"


@pytest.mark.parametrize(
    ("route", "kind"),
    [
        (_route("GET", "/v1/things/{id}"), "operation"),
        (_route("DELETE", "/v1/things/{id}"), "operation"),
        (_route("GET", "/v1/things/me"), "operation"),
        (_route("GET", "/v1/legacy"), "excused"),
        (_route("GET", "/__unit/info", internal=True), "internal"),
        (_route("GET", "/__vendor/stand-in"), "internal"),
        (_route("PUT", "/v1/things/{id}"), "undeclared"),
        (_route("GET", "/v1/other"), "undeclared"),
    ],
)
def test_every_route_is_exactly_one_kind(route: Route, kind: str) -> None:
    classified = SURFACE.classify(route)
    assert classified.kind == kind
    assert (classified.operation is not None) == (kind == "operation")
    if kind == "excused":
        assert classified.reason == "kept"
    if route.path == "/v1/things/me":
        assert classified.alias is not None
        assert classified.operation is not None
        assert classified.operation.key == "GET /v1/things/{thing_id}"


def test_response_schema_precedence_is_exact_then_default_then_envelope() -> None:
    get = EXTRACT.operation("GET", "/v1/things/{x}")
    delete = EXTRACT.operation("DELETE", "/v1/things/{x}")
    post = EXTRACT.operation("POST", "/v1/things")
    assert isinstance(get, Operation) and isinstance(delete, Operation) and isinstance(post, Operation)
    assert get.response_schema(200) == {"$ref": "#/components/schemas/Thing"}
    assert get.response_schema(400) is None
    assert get.response_schema(400, error_envelope="200") == {"$ref": "#/components/schemas/Thing"}
    assert delete.response_schema(418) == {"$ref": "#/components/schemas/Err"}
    # A declared status with no JSON content falls through to the envelope, not to None.
    assert post.response_schema(404, error_envelope="200") == {"$ref": "#/components/schemas/Thing"}
    assert post.request_schema() == {"$ref": "#/components/schemas/Thing"}
    assert get.request_schema() is None


def test_an_extract_must_be_openapi_3() -> None:
    with pytest.raises(ValueError, match="OpenAPI 3"):
        Extract({"swagger": "2.0", "paths": {}})


def test_a_declaration_needs_a_schema_and_a_source() -> None:
    with pytest.raises(ValueError, match='"schema": 1'):
        FidelityDeclaration.of("x", {"sources": [{"kind": "openapi3", "url": "u"}]})
    with pytest.raises(ValueError, match="at least one spec source"):
        FidelityDeclaration.of("x", {"schema": 1, "sources": []})
    with pytest.raises(ValueError, match="unknown spec source kind"):
        FidelityDeclaration.of("x", {"schema": 1, "sources": [{"kind": "wsdl", "url": "u"}]})


def test_loaders_name_the_missing_package() -> None:
    with pytest.raises(FileNotFoundError, match=r"declaration\.json"):
        load_declaration("vendorfake.fidelity")
    with pytest.raises(FileNotFoundError, match=r"extract\.json"):
        load_extract("vendorfake.fidelity")
