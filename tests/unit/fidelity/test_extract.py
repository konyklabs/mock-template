"""``cut_extract`` against synthetic documents: one OpenAPI 3, one Swagger 2.0.

Each is small enough to hold in one's head and exercises every rule the
cutter has: a modeled and an unmodeled operation, a schema closure that runs
through ``$ref``, ``items``, ``allOf`` and ``properties``, a dangling
reference, an unreachable schema, prose and ``x-*`` annotations everywhere,
and a *property* whose name is an annotation key. The Swagger 2.0 one adds
``basePath``, a body parameter, response schemas, ``#/definitions`` references
and a mapped vendor extension; the two together exercise the cross-source
merge. Both are invented here -- no byte of any vendor's document.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
import yaml

from vendorfake.fidelity.extract import cut_extract, render_json
from vendorfake.fidelity.types import Extract, SpecSource

URL = "https://example.test/spec.json"
SOURCE = SpecSource(kind="openapi3", url=URL)
SWAGGER_URL = "https://example.test/legacy-widgets.yaml"
SWAGGER_SOURCE = SpecSource(kind="swagger2", url=SWAGGER_URL, label="legacy")
EXTENSION_MAP = {"x-nullable": "nullable", "x-example-read-only": "readOnly"}


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
            "label": "spec",
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


def test_the_fragments_source_kind_is_refused_by_name() -> None:
    source = SpecSource(kind="fragments", url=URL)
    with pytest.raises(NotImplementedError, match=r"#57"):
        cut_extract([(source, b"{}")], MODELED, fetched="2026-09-02")


def test_a_document_that_is_not_what_its_kind_declares_is_refused() -> None:
    with pytest.raises(ValueError, match="openapi="):
        cut_extract([(SOURCE, b'{"swagger": "2.0"}')], MODELED, fetched="2026-09-02")
    with pytest.raises(ValueError, match="swagger="):
        cut_extract([(SWAGGER_SOURCE, b'{"openapi": "3.0.0"}')], MODELED, fetched="2026-09-02")
    with pytest.raises(ValueError, match="not a JSON or YAML document"):
        cut_extract([(SOURCE, b"{not: json")], MODELED, fetched="2026-09-02")
    with pytest.raises(ValueError, match="top level"):
        cut_extract([(SOURCE, b"not json")], MODELED, fetched="2026-09-02")


def test_no_sources_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least one"):
        cut_extract([], MODELED, fetched="2026-09-02")


def test_a_nullable_reference_is_cut_as_a_choice_the_validator_honours() -> None:
    """Deep-lens D8 (konyklabs/roadmap#55): ``nullable`` beside ``$ref`` is
    ignored by the validator, so a legal null failed. The cut rewrites it."""
    document = synthetic()
    document["components"]["schemas"]["Widget"]["properties"]["audit"] = {
        "$ref": "#/components/schemas/Audit",
        "nullable": True,
        "description": "prose",
    }
    cut = cut_extract([(SOURCE, blob(document))], MODELED, fetched="2026-09-02")
    assert cut["components"]["schemas"]["Widget"]["properties"]["audit"] == {
        "anyOf": [{"$ref": "#/components/schemas/Audit"}, {"enum": [None]}]
    }
    assert cut["x-vendorfake"]["rewritten"] == {"nullable_ref": 1, "extensions": {}}


# ---------------------------------------------------------------------------
# Swagger 2.0 (konyklabs/roadmap#56): converted to the OAS 3 shape, then cut.
# ---------------------------------------------------------------------------


def swagger2() -> dict[str, Any]:
    """A Swagger 2.0 document invented for these tests. ``Money`` and
    ``WidgetState`` are spelled exactly as ``synthetic()`` spells them (after
    stripping); ``Widget`` and ``Error`` are deliberately different."""
    return {
        "swagger": "2.0",
        "info": {"title": "Legacy Widgets", "version": "1.9", "description": "prose"},
        "host": "api.example.test",
        "basePath": "/legacy/v1",
        "schemes": ["https"],
        "consumes": ["application/json"],
        "produces": ["application/json"],
        "x-tooling": {"ignored": True},
        "parameters": {
            "Tenant": {"name": "X-Tenant", "in": "header", "required": True, "type": "string", "description": "prose"}
        },
        "paths": {
            "/widgets": {
                "parameters": [{"$ref": "#/parameters/Tenant"}],
                "post": {
                    "operationId": "createWidget",
                    "summary": "prose",
                    "x-release-status": "PUBLIC",
                    "parameters": [
                        {
                            "name": "body",
                            "in": "body",
                            "required": True,
                            "description": "prose",
                            "schema": {"$ref": "#/definitions/WidgetInput"},
                        },
                        {
                            "name": "dryRun",
                            "in": "query",
                            "type": "boolean",
                            "x-nullable": True,
                            "description": "prose",
                        },
                        {
                            "name": "tags",
                            "in": "query",
                            "type": "array",
                            "items": {"type": "string"},
                            "collectionFormat": "csv",
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {"$ref": "#/definitions/Widget"},
                            "examples": {"application/json": {"id": "W1"}},
                        },
                        "400": {"description": "bad request, no schema"},
                    },
                },
            },
            "/widgets/{widgetId}": {
                "get": {
                    "operationId": "getWidget",
                    "produces": ["text/plain", "application/json"],
                    "parameters": [{"name": "widgetId", "in": "path", "required": True, "type": "string"}],
                    "responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Widget"}}},
                },
                "delete": {"operationId": "deleteWidget", "responses": {"204": {"description": "gone"}}},
            },
            "/uploads": {
                "post": {
                    "operationId": "upload",
                    "consumes": ["multipart/form-data"],
                    "parameters": [
                        {"name": "file", "in": "formData", "type": "file", "required": True},
                        {"name": "note", "in": "formData", "type": "string"},
                    ],
                    "responses": {"201": {"description": "created"}},
                }
            },
        },
        "definitions": {
            "WidgetInput": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "price": {"$ref": "#/definitions/Money", "x-nullable": True},
                },
            },
            "Widget": {
                "type": "object",
                "title": "Widget",
                "x-vendor-hint": "strip me",
                "properties": {
                    "id": {"type": "string", "x-example-read-only": True},
                    "name": {"type": "string", "x-nullable": True, "description": "prose"},
                    "price": {"$ref": "#/definitions/Money"},
                    "state": {"$ref": "#/definitions/WidgetState"},
                    "description": {"type": "string"},
                },
            },
            "WidgetState": {"type": "string", "enum": ["OPEN", "CLOSED"], "x-wire-safe": True},
            "Money": {
                "type": "object",
                "properties": {
                    "amount": {"type": "integer", "format": "int64", "minimum": 0},
                    "currency": {"type": "string"},
                },
            },
            "Error": {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}},
            "Unreachable": {"type": "object"},
        },
    }


LEGACY_MODELED = [
    ("POST", "/legacy/v1/widgets"),
    ("GET", "/legacy/v1/widgets/{id}"),
    ("POST", "/legacy/v1/uploads"),
    ("PUT", "/legacy/v1/widgets/{id}"),
]


def content_of(cut: dict[str, Any]) -> dict[str, Any]:
    """The cut minus its source rows: what two encodings of one document share."""
    meta = {key: value for key, value in cut["x-vendorfake"].items() if key != "sources"}
    return {**{key: value for key, value in cut.items() if key != "x-vendorfake"}, "x-vendorfake": meta}


@pytest.fixture
def legacy() -> dict[str, Any]:
    return cut_extract(
        [(SWAGGER_SOURCE, blob(swagger2()))], LEGACY_MODELED, fetched="2026-09-02", extension_map=EXTENSION_MAP
    )


def test_swagger2_base_path_prefixes_every_kept_path_and_the_cut_is_oas3(legacy: dict[str, Any]) -> None:
    assert set(legacy["paths"]) == {"/legacy/v1/widgets", "/legacy/v1/widgets/{widgetId}", "/legacy/v1/uploads"}
    assert legacy["x-vendorfake"]["modeled"] == [
        "GET /legacy/v1/widgets/{widgetId}",
        "POST /legacy/v1/uploads",
        "POST /legacy/v1/widgets",
    ]
    assert legacy["x-vendorfake"]["missing"] == ["PUT /legacy/v1/widgets/{id}"]
    assert legacy["openapi"] == "3.0.3"
    assert legacy["info"] == {"title": "Legacy Widgets (scoped extract)", "version": "1.9"}
    Extract(json.loads(render_json(legacy)))  # the types module accepts it as OAS 3


def test_swagger2_body_parameter_becomes_request_body_and_the_rest_stay_parameters(legacy: dict[str, Any]) -> None:
    post = legacy["paths"]["/legacy/v1/widgets"]["post"]
    assert post["operationId"] == "createWidget"
    assert post["requestBody"] == {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WidgetInput"}}},
    }
    # Path-level shared parameter first (resolved through #/parameters), then the
    # operation's own; type keywords wrapped into ``schema``; collectionFormat dropped.
    assert post["parameters"] == [
        {"name": "X-Tenant", "in": "header", "required": True, "schema": {"type": "string"}},
        {"name": "dryRun", "in": "query", "schema": {"type": "boolean", "nullable": True}},
        {"name": "tags", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
    ]
    assert "collectionFormat" in legacy["x-vendorfake"]["stripped"]


def test_swagger2_response_schema_moves_under_content_with_the_produces_media_type(legacy: dict[str, Any]) -> None:
    post = legacy["paths"]["/legacy/v1/widgets"]["post"]
    assert post["responses"]["200"] == {
        "description": "200",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}},
    }
    assert post["responses"]["400"] == {"description": "400"}
    # ``produces`` lists text/plain first; the JSON entry is the one the validator reads.
    get = legacy["paths"]["/legacy/v1/widgets/{widgetId}"]["get"]
    assert list(get["responses"]["200"]["content"]) == ["application/json"]
    assert get["parameters"] == [{"name": "widgetId", "in": "path", "required": True, "schema": {"type": "string"}}]


def test_swagger2_form_parameters_become_a_form_request_body(legacy: dict[str, Any]) -> None:
    upload = legacy["paths"]["/legacy/v1/uploads"]["post"]
    assert "parameters" not in upload
    assert upload["requestBody"] == {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {"file": {"type": "file"}, "note": {"type": "string"}},
                    "required": ["file"],
                }
            }
        },
    }


def test_swagger2_definitions_become_component_schemas_with_every_reference_rewritten(
    legacy: dict[str, Any],
) -> None:
    assert set(legacy["components"]["schemas"]) == {"WidgetInput", "Widget", "WidgetState", "Money"}
    assert "#/definitions/" not in render_json(legacy)
    assert legacy["x-vendorfake"]["stubbed"] == []


def test_mapped_extensions_are_renamed_before_the_strip_and_unmapped_ones_are_stripped(
    legacy: dict[str, Any],
) -> None:
    widget = legacy["components"]["schemas"]["Widget"]
    assert widget["properties"]["id"] == {"type": "string", "readOnly": True}
    assert widget["properties"]["name"] == {"type": "string", "nullable": True}
    assert widget["properties"]["description"] == {"type": "string"}  # a property, not prose
    assert "title" not in widget
    assert legacy["components"]["schemas"]["WidgetState"] == {"type": "string", "enum": ["OPEN", "CLOSED"]}
    text = render_json({key: value for key, value in legacy.items() if key != "x-vendorfake"})
    for key in ("x-vendor-hint", "x-wire-safe", "x-nullable", "x-example-read-only", "x-release-status", "x-tooling"):
        assert f'"{key}"' not in text, key
    assert {"x-vendor-hint", "x-wire-safe"} <= set(legacy["x-vendorfake"]["stripped"])
    assert "x-nullable" not in legacy["x-vendorfake"]["stripped"]
    # A mapped ``x-nullable`` beside ``$ref`` goes through the same rewrite a native one does.
    assert legacy["components"]["schemas"]["WidgetInput"]["properties"]["price"] == {
        "anyOf": [{"$ref": "#/components/schemas/Money"}, {"enum": [None]}]
    }
    assert legacy["x-vendorfake"]["rewritten"] == {
        "nullable_ref": 1,
        "extensions": {"x-nullable": 3, "x-example-read-only": 1},
    }


def test_without_a_map_the_same_extensions_are_stripped_like_any_other() -> None:
    cut = cut_extract([(SWAGGER_SOURCE, blob(swagger2()))], LEGACY_MODELED, fetched="2026-09-02")
    widget = cut["components"]["schemas"]["Widget"]
    assert widget["properties"]["id"] == {"type": "string"}
    assert widget["properties"]["name"] == {"type": "string"}
    assert {"x-nullable", "x-example-read-only"} <= set(cut["x-vendorfake"]["stripped"])
    assert cut["x-vendorfake"]["rewritten"] == {"nullable_ref": 0, "extensions": {}}


def test_an_openapi3_source_applies_the_extension_map_too() -> None:
    document = synthetic()
    document["components"]["schemas"]["Money"]["properties"]["currency"]["x-nullable"] = True
    cut = cut_extract([(SOURCE, blob(document))], MODELED, fetched="2026-09-02", extension_map=EXTENSION_MAP)
    assert cut["components"]["schemas"]["Money"]["properties"]["currency"] == {"type": "string", "nullable": True}
    assert cut["x-vendorfake"]["rewritten"]["extensions"] == {"x-nullable": 1}


def test_the_declared_base_path_overrides_the_documents() -> None:
    source = SpecSource(kind="swagger2", url=SWAGGER_URL, base_path="/other")
    cut = cut_extract([(source, blob(swagger2()))], [("POST", "/other/widgets")], fetched="2026-09-02")
    assert list(cut["paths"]) == ["/other/widgets"]
    # A source without a label is labelled by its file stem.
    assert cut["x-vendorfake"]["sources"][0]["label"] == "legacy-widgets"


def test_an_openapi3_servers_path_is_the_base_path_when_none_is_declared() -> None:
    document = synthetic()
    document["servers"] = [{"url": "https://api.example.test/stock"}]
    cut = cut_extract([(SOURCE, blob(document))], [("POST", "/stock/v1/widgets")], fetched="2026-09-02")
    assert list(cut["paths"]) == ["/stock/v1/widgets"]
    assert cut["x-vendorfake"]["missing"] == []


def test_a_yaml_encoding_cuts_to_the_same_content_as_json() -> None:
    as_yaml = yaml.safe_dump(swagger2(), sort_keys=False).encode("utf-8")
    from_yaml = cut_extract(
        [(SWAGGER_SOURCE, as_yaml)], LEGACY_MODELED, fetched="2026-09-02", extension_map=EXTENSION_MAP
    )
    from_json = cut_extract(
        [(SWAGGER_SOURCE, blob(swagger2()))], LEGACY_MODELED, fetched="2026-09-02", extension_map=EXTENSION_MAP
    )
    assert content_of(from_yaml) == content_of(from_json)
    assert from_yaml["x-vendorfake"]["sources"][0]["bytes"] == len(as_yaml)


def test_yaml_integer_status_keys_and_bare_scalars_read_as_json_would() -> None:
    text = """
swagger: 2.0
info: {title: Bare, version: 1.0}
basePath: /b
paths:
  /things:
    get:
      operationId: listThings
      responses:
        200:
          description: ok
          schema: {$ref: '#/definitions/Thing'}
definitions:
  Thing:
    type: object
    properties:
      since: {type: string, format: date, default: 2024-01-31}
"""
    cut = cut_extract([(SWAGGER_SOURCE, text.encode("utf-8"))], [("GET", "/b/things")], fetched="2026-09-02")
    assert list(cut["paths"]["/b/things"]["get"]["responses"]) == ["200"]
    assert cut["info"]["version"] == "1.0"
    assert cut["components"]["schemas"]["Thing"]["properties"]["since"]["default"] == "2024-01-31"
    json.loads(render_json(cut))  # nothing YAML-typed survived into the document


# ---------------------------------------------------------------------------
# Several sources: first hit wins, identical schemas dedupe, differing ones namespace.
# ---------------------------------------------------------------------------


def two_sources() -> list[tuple[SpecSource, bytes]]:
    """OAS 3 first, Swagger 2.0 second. ``Problem`` is spelled identically in
    both but references ``Error``, which differs -- the fixed-point case."""
    first = synthetic()
    first["components"]["schemas"]["Problem"] = {
        "type": "object",
        "properties": {"error": {"$ref": "#/components/schemas/Error"}},
    }
    first["components"]["schemas"]["CreateWidgetResponse"]["properties"]["problem"] = {
        "$ref": "#/components/schemas/Problem"
    }
    second = swagger2()
    second["definitions"]["Problem"] = {"type": "object", "properties": {"error": {"$ref": "#/definitions/Error"}}}
    second["paths"]["/widgets"]["post"]["responses"]["400"]["schema"] = {"$ref": "#/definitions/Problem"}
    # Referenced here, defined only by the first source: resolves there, not a stub.
    second["definitions"]["Widget"]["properties"]["audit"] = {"$ref": "#/definitions/Audit"}
    return [(SOURCE, blob(first)), (SWAGGER_SOURCE, blob(second))]


TWO_SOURCE_MODELED = [
    ("POST", "/v1/widgets"),
    ("GET", "/v1/widgets/{id}"),
    ("POST", "/legacy/v1/widgets"),
    ("GET", "/legacy/v1/widgets/{id}"),
]


@pytest.fixture
def merged() -> dict[str, Any]:
    return cut_extract(two_sources(), TWO_SOURCE_MODELED, fetched="2026-09-02", extension_map=EXTENSION_MAP)


def test_operations_are_found_across_sources_and_each_source_row_carries_its_label(merged: dict[str, Any]) -> None:
    assert merged["x-vendorfake"]["modeled"] == [
        "GET /legacy/v1/widgets/{widgetId}",
        "GET /v1/widgets/{widget_id}",
        "POST /legacy/v1/widgets",
        "POST /v1/widgets",
    ]
    assert merged["x-vendorfake"]["missing"] == []
    assert [row["label"] for row in merged["x-vendorfake"]["sources"]] == ["spec", "legacy"]
    assert [row["url"] for row in merged["x-vendorfake"]["sources"]] == [URL, SWAGGER_URL]
    assert merged["openapi"] == "3.0.0"  # the first source's


def test_identical_schemas_dedupe_and_differing_ones_are_namespaced_for_the_later_source(
    merged: dict[str, Any],
) -> None:
    schemas = merged["components"]["schemas"]
    assert "legacy.Money" not in schemas and "legacy.WidgetState" not in schemas
    assert schemas["Money"]["properties"]["amount"] == {"type": "integer", "format": "int64", "minimum": 0}
    assert set(schemas["Error"]["properties"]) == {"code", "detail"}
    assert set(schemas["legacy.Error"]["properties"]) == {"status", "message"}
    assert set(schemas["legacy.Widget"]["properties"]) == {"id", "name", "price", "state", "description", "audit"}
    assert schemas["legacy.Widget"]["properties"]["price"] == {"$ref": "#/components/schemas/Money"}
    assert schemas["legacy.Widget"]["properties"]["audit"] == {"$ref": "#/components/schemas/Audit"}
    # Problem is identical text in both sources, but the later one now points at legacy.Error.
    assert schemas["Problem"] == {"type": "object", "properties": {"error": {"$ref": "#/components/schemas/Error"}}}
    assert schemas["legacy.Problem"] == {
        "type": "object",
        "properties": {"error": {"$ref": "#/components/schemas/legacy.Error"}},
    }
    assert merged["x-vendorfake"]["namespaced"] == {
        "legacy.Error": "legacy",
        "legacy.Problem": "legacy",
        "legacy.Widget": "legacy",
    }
    assert merged["x-vendorfake"]["stubbed"] == ["Missing"]  # Audit resolved to the first source


def test_the_later_sources_operations_follow_the_namespacing(merged: dict[str, Any]) -> None:
    post = merged["paths"]["/legacy/v1/widgets"]["post"]
    assert post["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/legacy.Widget"
    }
    assert post["responses"]["400"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/legacy.Problem"
    }
    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/WidgetInput"}
    first = merged["paths"]["/v1/widgets"]["post"]
    assert first["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CreateWidgetResponse"
    }
    extract = Extract(json.loads(render_json(merged)))
    assert extract.operation("POST", "/legacy/v1/widgets") is not None
    assert extract.operation("POST", "/v1/widgets") is not None


def test_a_multi_source_cut_is_byte_identical_across_runs() -> None:
    first = render_json(
        cut_extract(two_sources(), TWO_SOURCE_MODELED, fetched="2026-09-02", extension_map=EXTENSION_MAP)
    )
    second = render_json(
        cut_extract(
            two_sources(), list(reversed(TWO_SOURCE_MODELED)), fetched="2026-09-02", extension_map=EXTENSION_MAP
        )
    )
    assert first == second


# ---------------------------------------------------------------------------
# The declared error schema is a root; a declaration naming one nobody defines is an error.
# ---------------------------------------------------------------------------


def test_the_error_schema_is_kept_even_when_no_operation_references_it() -> None:
    cut = cut_extract([(SWAGGER_SOURCE, blob(swagger2()))], LEGACY_MODELED, fetched="2026-09-02", error_schema="Error")
    assert cut["components"]["schemas"]["Error"] == {
        "type": "object",
        "properties": {"status": {"type": "string"}, "message": {"type": "string"}},
    }
    assert "Unreachable" not in cut["components"]["schemas"]
    assert cut["x-vendorfake"]["stubbed"] == []


def test_the_error_schema_may_live_in_a_later_source_only() -> None:
    first = synthetic()
    del first["components"]["schemas"]["Error"]
    first["components"]["schemas"]["CreateWidgetResponse"]["properties"].pop("errors")
    cut = cut_extract(
        [(SOURCE, blob(first)), (SWAGGER_SOURCE, blob(swagger2()))],
        [("POST", "/v1/widgets")],
        fetched="2026-09-02",
        error_schema="Error",
    )
    assert set(cut["components"]["schemas"]["Error"]["properties"]) == {"status", "message"}
    assert cut["x-vendorfake"]["namespaced"] == {}


def test_a_declared_error_schema_no_source_defines_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"error schema 'Nope'.*legacy-widgets\.yaml"):
        cut_extract([(SWAGGER_SOURCE, blob(swagger2()))], LEGACY_MODELED, fetched="2026-09-02", error_schema="Nope")


def test_the_cut_does_not_mutate_a_swagger2_input() -> None:
    document = swagger2()
    before = copy.deepcopy(document)
    cut_extract([(SWAGGER_SOURCE, blob(document))], LEGACY_MODELED, fetched="2026-09-02", extension_map=EXTENSION_MAP)
    assert document == before


def test_yaml_yes_and_no_stay_strings() -> None:
    """PyYAML implements YAML 1.1, where ``YES``/``NO`` are booleans; a vendor's
    enum of those words must survive as the strings the document means."""
    document = (
        b"swagger: '2.0'\ninfo: {title: t, version: '1'}\nbasePath: /v1\npaths:\n  /things:\n    get:\n"
        b"      responses:\n        '200':\n          schema: {$ref: '#/definitions/Thing'}\n"
        b"definitions:\n  Thing:\n    type: object\n    properties:\n      charge: {type: string, enum: [YES, NO]}\n"
        b"      on: {type: boolean, default: true}\n"
    )
    cut = cut_extract(
        [(SpecSource(kind="swagger2", url="https://example.test/s.yaml"), document)],
        [("GET", "/v1/things")],
        fetched="2026-09-02",
    )
    props = cut["components"]["schemas"]["Thing"]["properties"]
    assert props["charge"]["enum"] == ["YES", "NO"]
    assert props["on"]["default"] is True
