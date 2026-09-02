"""``cut_extract`` against a synthetic OpenAPI 3 document.

The document is small enough to hold in one's head and exercises every rule
the cutter has: a modeled and an unmodeled operation, a schema closure that
runs through ``$ref``, ``items``, ``allOf`` and ``properties``, a dangling
reference, an unreachable schema, prose and ``x-*`` annotations everywhere,
and a *property* whose name is an annotation key.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

from vendorfake.fidelity.extract import cut_extract, render_json
from vendorfake.fidelity.types import Extract, SpecSource

URL = "https://example.test/spec.json"
SOURCE = SpecSource(kind="openapi3", url=URL)


def synthetic() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Widgets", "version": "2.3.4", "description": "prose"},
        "servers": [{"url": "https://api.example.test"}],
        "x-tooling": {"ignored": True},
        "paths": {
            "/v1/widgets": {
                "post": {
                    "operationId": "CreateWidget",
                    "summary": "CreateWidget",
                    "description": "prose",
                    "tags": ["Widgets"],
                    "security": [{"oauth2": ["WIDGETS_WRITE"]}],
                    "x-release-status": "PUBLIC",
                    "parameters": [],
                    "requestBody": {
                        "required": True,
                        "description": "prose",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateWidgetRequest"},
                                "example": {"name": "x"},
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CreateWidgetResponse"},
                                    "examples": {"a": {"value": {}}},
                                }
                            },
                        }
                    },
                }
            },
            "/v1/widgets/{widget_id}": {
                "get": {
                    "operationId": "RetrieveWidget",
                    "deprecated": True,
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "description": "prose",
                            "schema": {"type": "string", "example": "W1"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/RetrieveWidgetResponse"}}
                            },
                        },
                        "204": {"description": "nothing"},
                    },
                },
                "delete": {
                    "operationId": "DeleteWidget",
                    "parameters": [{"name": "widget_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Unreachable"}}},
                        }
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "CreateWidgetRequest": {
                    "type": "object",
                    "title": "CreateWidgetRequest",
                    "description": "prose",
                    "x-release-status": "PUBLIC",
                    "required": ["widget"],
                    "properties": {"widget": {"$ref": "#/components/schemas/Widget"}},
                },
                "CreateWidgetResponse": {
                    "type": "object",
                    "properties": {
                        "widget": {"$ref": "#/components/schemas/Widget"},
                        "errors": {"type": "array", "items": {"$ref": "#/components/schemas/Error"}},
                    },
                },
                "RetrieveWidgetResponse": {
                    "allOf": [
                        {"$ref": "#/components/schemas/CreateWidgetResponse"},
                        {"type": "object", "properties": {"audit": {"$ref": "#/components/schemas/Audit"}}},
                    ]
                },
                "Widget": {
                    "type": "object",
                    "description": "prose",
                    "properties": {
                        "id": {"type": "string", "readOnly": True, "description": "prose"},
                        "description": {
                            "type": "string",
                            "nullable": True,
                            "description": "a property named description",
                        },
                        "title": {"type": "string"},
                        "state": {"$ref": "#/components/schemas/WidgetState", "x-enum-elements": []},
                        "price": {"$ref": "#/components/schemas/Money"},
                        "tags": {"type": "array", "items": {"type": "string"}, "example": ["a"]},
                        "extra": {"type": "object", "additionalProperties": {"$ref": "#/components/schemas/Money"}},
                    },
                },
                "WidgetState": {"type": "string", "enum": ["OPEN", "CLOSED"], "x-enum-elements": [{"name": "OPEN"}]},
                "Money": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer", "format": "int64", "minimum": 0},
                        "currency": {"type": "string"},
                    },
                },
                "Error": {
                    "type": "object",
                    "properties": {"code": {"type": "string"}, "detail": {"type": "string", "nullable": True}},
                },
                "Audit": {"type": "object", "properties": {"by": {"$ref": "#/components/schemas/Missing"}}},
                "Unreachable": {"type": "object", "properties": {"never": {"type": "string"}}},
            }
        },
    }


def blob(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")


MODELED = [("POST", "/v1/widgets"), ("GET", "/v1/widgets/{id}"), ("PUT", "/v1/widgets/{id}")]


@pytest.fixture
def cut() -> dict[str, Any]:
    return cut_extract([(SOURCE, blob(synthetic()))], MODELED, fetched="2026-09-02")


def test_only_modeled_operations_are_kept_and_the_rest_are_listed(cut: dict[str, Any]) -> None:
    assert set(cut["paths"]) == {"/v1/widgets", "/v1/widgets/{widget_id}"}
    assert set(cut["paths"]["/v1/widgets"]) == {"post"}
    # DELETE exists upstream but is not modeled; PUT is modeled but does not exist upstream.
    assert set(cut["paths"]["/v1/widgets/{widget_id}"]) == {"get"}
    assert cut["x-vendorfake"]["modeled"] == ["GET /v1/widgets/{widget_id}", "POST /v1/widgets"]
    assert cut["x-vendorfake"]["missing"] == ["PUT /v1/widgets/{id}"]


def test_parameter_names_do_not_matter_but_the_upstream_spelling_is_kept(cut: dict[str, Any]) -> None:
    operation = cut["paths"]["/v1/widgets/{widget_id}"]["get"]
    assert operation["parameters"] == [
        {"name": "widget_id", "in": "path", "required": True, "schema": {"type": "string"}}
    ]
    assert operation["operationId"] == "RetrieveWidget"
    assert operation["deprecated"] is True


def test_schema_closure_runs_through_ref_items_allof_properties_and_additional_properties(
    cut: dict[str, Any],
) -> None:
    assert set(cut["components"]["schemas"]) == {
        "CreateWidgetRequest",
        "CreateWidgetResponse",
        "RetrieveWidgetResponse",
        "Widget",
        "WidgetState",
        "Money",
        "Error",
        "Audit",
        "Missing",
    }
    assert "Unreachable" not in cut["components"]["schemas"]


def test_dangling_reference_is_stubbed_and_listed(cut: dict[str, Any]) -> None:
    assert cut["components"]["schemas"]["Missing"] == {}
    assert cut["x-vendorfake"]["stubbed"] == ["Missing"]


def test_prose_and_vendor_extensions_are_stripped_but_named_properties_survive(cut: dict[str, Any]) -> None:
    # The ledger under x-vendorfake names the stripped keys; the document proper must not.
    text = render_json({key: value for key, value in cut.items() if key != "x-vendorfake"})
    for key in ("summary", "example", "examples", "externalDocs", "x-release-status", "x-enum-elements", "x-tooling"):
        assert f'"{key}"' not in text, key
    widget = cut["components"]["schemas"]["Widget"]
    assert "description" not in widget
    assert set(widget["properties"]) == {"id", "description", "title", "state", "price", "tags", "extra"}
    assert widget["properties"]["description"] == {"type": "string", "nullable": True}
    assert widget["properties"]["state"] == {"$ref": "#/components/schemas/WidgetState"}
    assert "title" not in cut["components"]["schemas"]["CreateWidgetRequest"]
    assert cut["components"]["schemas"]["WidgetState"] == {"type": "string", "enum": ["OPEN", "CLOSED"]}
    assert cut["components"]["schemas"]["Money"]["properties"]["amount"] == {
        "type": "integer",
        "format": "int64",
        "minimum": 0,
    }
    stripped = cut["x-vendorfake"]["stripped"]
    assert {"description", "summary", "example", "examples", "title", "tags", "security", "x-release-status"} <= set(
        stripped
    )
    assert stripped == sorted(stripped)


def test_info_title_is_marked_and_the_version_is_upstreams(cut: dict[str, Any]) -> None:
    assert cut["openapi"] == "3.0.0"
    assert cut["info"] == {"title": "Widgets (scoped extract)", "version": "2.3.4"}
    assert "servers" not in cut


def test_responses_carry_the_status_as_description_and_request_body_keeps_required(cut: dict[str, Any]) -> None:
    post = cut["paths"]["/v1/widgets"]["post"]
    assert post["requestBody"] == {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateWidgetRequest"}}},
    }
    assert post["responses"]["200"] == {
        "description": "200",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateWidgetResponse"}}},
    }
    get = cut["paths"]["/v1/widgets/{widget_id}"]["get"]
    assert get["responses"]["204"] == {"description": "204"}


def test_source_row_pins_the_exact_bytes(cut: dict[str, Any]) -> None:
    data = blob(synthetic())
    assert cut["x-vendorfake"]["sources"] == [
        {
            "url": URL,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "version": "2.3.4",
            "fetched": "2026-09-02",
        }
    ]
    assert cut["x-vendorfake"]["schema"] == 1


def test_two_cuts_of_the_same_input_are_byte_identical() -> None:
    first = render_json(cut_extract([(SOURCE, blob(synthetic()))], MODELED, fetched="2026-09-02"))
    reordered = list(reversed(MODELED))
    second = render_json(cut_extract([(SOURCE, blob(synthetic()))], reordered, fetched="2026-09-02"))
    assert first == second
    assert first.endswith("}\n")
    assert json.loads(first) == json.loads(second)


def test_the_cut_does_not_mutate_its_input() -> None:
    document = synthetic()
    before = copy.deepcopy(document)
    cut_extract([(SOURCE, blob(document))], MODELED, fetched="2026-09-02")
    assert document == before


def test_the_types_module_reads_the_cut_back(cut: dict[str, Any]) -> None:
    extract = Extract(json.loads(render_json(cut)))
    operation = extract.operation("GET", "/v1/widgets/{anything}")
    assert operation is not None
    assert operation.key == "GET /v1/widgets/{widget_id}"
    assert operation.response_schema(200) == {"$ref": "#/components/schemas/RetrieveWidgetResponse"}
    assert extract.metadata["stubbed"] == ["Missing"]
    assert extract.operation("PUT", "/v1/widgets/{id}") is None


def test_base_path_is_stripped_for_lookup_and_added_back_in_the_extract() -> None:
    prefixed = SpecSource(kind="openapi3", url=URL, base_path="/api")
    cut = cut_extract([(prefixed, blob(synthetic()))], [("POST", "/api/v1/widgets")], fetched="2026-09-02")
    assert list(cut["paths"]) == ["/api/v1/widgets"]
    assert cut["x-vendorfake"]["modeled"] == ["POST /api/v1/widgets"]


def test_a_local_parameter_reference_is_resolved_inline() -> None:
    document = synthetic()
    document["components"]["parameters"] = {
        "WidgetId": {"name": "widget_id", "in": "path", "required": True, "schema": {"type": "string"}}
    }
    document["paths"]["/v1/widgets/{widget_id}"]["get"]["parameters"] = [{"$ref": "#/components/parameters/WidgetId"}]
    cut = cut_extract([(SOURCE, blob(document))], [("GET", "/v1/widgets/{id}")], fetched="2026-09-02")
    assert cut["paths"]["/v1/widgets/{widget_id}"]["get"]["parameters"] == [
        {"name": "widget_id", "in": "path", "required": True, "schema": {"type": "string"}}
    ]
    assert "components" not in cut or "parameters" not in cut["components"]


@pytest.mark.parametrize("kind", ["swagger2", "fragments"])
def test_other_source_kinds_are_refused_by_name(kind: str) -> None:
    source = SpecSource(kind=kind, url=URL)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match=r"#56.*#57"):
        cut_extract([(source, b"{}")], MODELED, fetched="2026-09-02")


def test_a_document_that_is_not_openapi3_is_refused() -> None:
    with pytest.raises(ValueError, match="openapi="):
        cut_extract([(SOURCE, b'{"swagger": "2.0"}')], MODELED, fetched="2026-09-02")
    with pytest.raises(ValueError, match="not a JSON document"):
        cut_extract([(SOURCE, b"not json")], MODELED, fetched="2026-09-02")


def test_no_sources_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least one"):
        cut_extract([], MODELED, fetched="2026-09-02")
