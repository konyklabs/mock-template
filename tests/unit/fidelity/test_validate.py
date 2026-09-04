"""The validating client, driven over a synthetic vendor and a hand-written extract.

The vendor has one route per classification the surface can produce -- an
operation reached by its own path, one reached through an alias, an excused
one, an undeclared one, a control-plane one -- plus one whose handler answers
whatever the test scripts, so each schema error can be produced on demand.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from tests.fakes import make_unit, route
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.reply import json_, no_content, text
from vendorfake.core.kernel.router import Router
from vendorfake.core.kernel.types import ReplyInit
from vendorfake.core.transport.inprocess import InProcessResponse, in_process
from vendorfake.fidelity.types import (
    Alias,
    Deviation,
    Excuse,
    Extract,
    FidelityDeclaration,
    Override,
    SpecSource,
    Surface,
)
from vendorfake.fidelity.validate import (
    FidelityViolation,
    Ledger,
    LedgerRow,
    UndeclaredRoute,
    ValidatingClient,
)

# ---------------------------------------------------------------------------
# The extract: OpenAPI 3.0, four operations, one $ref chain, one enum, one
# nullable, one required. The order path's parameter is deliberately named
# differently from the unit's, because the surface matches on position.
# ---------------------------------------------------------------------------


def _json_response(ref: str) -> dict[str, Any]:
    return {"description": "200", "content": {"application/json": {"schema": {"$ref": ref}}}}


EXTRACT: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Synthetic (scoped extract)", "version": "1.0"},
    "paths": {
        "/v2/orders/{id}": {
            "get": {
                "operationId": "RetrieveOrder",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": _json_response("#/components/schemas/RetrieveOrderResponse")},
            }
        },
        "/v2/orders": {
            "post": {
                "operationId": "CreateOrder",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateOrderRequest"}}},
                },
                "responses": {"200": _json_response("#/components/schemas/CreateOrderResponse")},
            }
        },
        "/v2/merchants/{merchant_id}": {
            "get": {
                "operationId": "RetrieveMerchant",
                "responses": {"200": _json_response("#/components/schemas/RetrieveMerchantResponse")},
            }
        },
        "/v2/plain": {
            "get": {
                "operationId": "Plain",
                "responses": {"200": {"description": "200", "content": {"text/plain": {"schema": {"type": "string"}}}}},
            }
        },
    },
    "components": {
        "schemas": {
            "Error": {
                "type": "object",
                "required": ["category", "code"],
                "properties": {
                    "category": {"type": "string"},
                    "code": {"type": "string"},
                    "field": {"type": "string", "nullable": True},
                },
            },
            "LineItem": {
                "type": "object",
                "required": ["quantity"],
                "properties": {"quantity": {"type": "string"}},
            },
            "Order": {
                "type": "object",
                "required": ["id", "state"],
                "properties": {
                    "id": {"type": "string", "readOnly": True},
                    "state": {"type": "string", "enum": ["OPEN", "COMPLETED"]},
                    "version": {"type": "integer"},
                    "closed_at": {"type": "string", "nullable": True},
                    "note": {"type": "string"},
                    "line_items": {"type": "array", "items": {"$ref": "#/components/schemas/LineItem"}},
                },
            },
            "CreateOrderRequest": {
                "type": "object",
                "required": ["idempotency_key"],
                "properties": {
                    "idempotency_key": {"type": "string"},
                    "note": {"type": "string"},
                    "state": {"type": "string", "enum": ["OPEN", "COMPLETED"]},
                },
            },
            "RetrieveOrderResponse": {
                "type": "object",
                "properties": {
                    "errors": {"type": "array", "items": {"$ref": "#/components/schemas/Error"}},
                    "order": {"$ref": "#/components/schemas/Order"},
                },
            },
            "CreateOrderResponse": {
                "type": "object",
                "properties": {
                    "errors": {"type": "array", "items": {"$ref": "#/components/schemas/Error"}},
                    "order": {"$ref": "#/components/schemas/Order"},
                },
            },
            "RetrieveMerchantResponse": {
                "type": "object",
                "required": ["merchant"],
                "properties": {
                    "merchant": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}, "country": {"type": "string"}},
                    }
                },
            },
        }
    },
    "x-vendorfake": {"schema": 1, "sources": [], "modeled": [], "missing": [], "stubbed": [], "stripped": []},
}

DECLARATION = FidelityDeclaration(
    anchor="tests.synthetic",
    sources=(SpecSource(kind="openapi3", url="https://example.invalid/openapi.json"),),
    aliases=(
        Alias(
            method="GET",
            path="/v2/merchants/me",
            spec_path="/v2/merchants/{merchant_id}",
            reason="the documented literal for the caller's own merchant",
        ),
    ),
    excused=(Excuse(method="GET", path="/v2/legacy/ping", reason="kept for a client that still calls it"),),
    error_envelope="200",
    error_member="errors",
)

GOOD_ORDER = {
    "order": {
        "id": "ord_1",
        "state": "OPEN",
        "version": 1,
        "closed_at": None,
        "note": "a note",
        "line_items": [{"quantity": "1"}],
    }
}


# ---------------------------------------------------------------------------
# The vendor: each route answers what the script says.
# ---------------------------------------------------------------------------


@dataclass
class Script:
    """What the next answer is. Tests mutate it between calls."""

    status: int = 200
    body: object = None
    reply: ReplyInit | None = None

    def answer(self, args: Any) -> ReplyInit:
        if self.reply is not None:
            return self.reply
        return json_(self.body, status=self.status)


@dataclass
class World:
    unit: Any
    script: Script
    ledger: Ledger
    client: ValidatingClient


def _build(*, strict_undeclared: bool = True, validate_requests: bool = False) -> World:
    script = Script()
    unit = make_unit(
        [
            route("GET", "/v2/orders/{order_id}", script.answer),
            route("POST", "/v2/orders", script.answer),
            route("GET", "/v2/merchants/me", script.answer),
            route("GET", "/v2/plain", script.answer),
            route("GET", "/v2/legacy/ping", script.answer),
            route("GET", "/v2/surprise", script.answer),
        ],
        control_routes=control_plane_routes,
    )
    ledger = Ledger()
    surface = Surface(DECLARATION, Extract(EXTRACT))
    return World(
        unit,
        script,
        ledger,
        ValidatingClient(
            unit, surface, ledger, strict_undeclared=strict_undeclared, validate_requests=validate_requests
        ),
    )


@pytest.fixture
def world() -> Any:
    built = _build()
    try:
        yield built
    finally:
        built.unit.stop()


@pytest.fixture
def lenient() -> Any:
    built = _build(strict_undeclared=False)
    try:
        yield built
    finally:
        built.unit.stop()


# ---------------------------------------------------------------------------
# Conforming bodies pass, and the response is the plain client's response.
# ---------------------------------------------------------------------------


def test_conforming_body_passes_and_is_counted(world: World) -> None:
    world.script.body = GOOD_ORDER
    res = world.client.get("/v2/orders/ord_1")
    assert res.status == 200
    assert res.json() == GOOD_ORDER
    assert world.ledger.row("GET /v2/orders/{order_id}") == LedgerRow("GET /v2/orders/{order_id}", validated=1)


def test_response_is_returned_unchanged(world: World) -> None:
    world.script.body = GOOD_ORDER
    checked = world.client.get("/v2/orders/ord_1")
    plain = in_process(world.unit).get("/v2/orders/ord_1")
    assert isinstance(checked, InProcessResponse)
    assert checked.status == plain.status
    assert checked.body == plain.body
    # The unit mints one request id per call; everything else must be identical.
    assert {k: v for k, v in checked.headers.items() if k != "x-unit-request-id"} == {
        k: v for k, v in plain.headers.items() if k != "x-unit-request-id"
    }


# ---------------------------------------------------------------------------
# Each kind of schema error names the pointer.
# ---------------------------------------------------------------------------


def _violation(world: World, path: str, body: object, *, method: str = "GET", status: int = 200) -> FidelityViolation:
    world.script.body = body
    world.script.status = status
    with pytest.raises(FidelityViolation) as caught:
        world.client.call(method=method, path=path)
    return caught.value


def test_wrong_type_names_the_pointer(world: World) -> None:
    body = {"order": {**GOOD_ORDER["order"], "version": "one"}}
    exc = _violation(world, "/v2/orders/ord_1", body)
    assert exc.route_key == "GET /v2/orders/{order_id}"
    assert exc.operation_key == "GET /v2/orders/{id}"
    assert exc.status == 200
    assert exc.errors == ("/order/version: 'one' is not of type 'integer'",)
    assert "/order/version" in str(exc)
    assert isinstance(exc, AssertionError)


def test_bad_enum_names_the_pointer(world: World) -> None:
    body = {"order": {**GOOD_ORDER["order"], "state": "BOGUS"}}
    exc = _violation(world, "/v2/orders/ord_1", body)
    assert len(exc.errors) == 1
    assert exc.errors[0].startswith("/order/state: 'BOGUS' is not one of")
    assert "/order/state" in str(exc)


def test_null_where_not_nullable_is_a_violation(world: World) -> None:
    body = {"order": {**GOOD_ORDER["order"], "note": None}}
    exc = _violation(world, "/v2/orders/ord_1", body)
    # The OAS 3.0 ``type`` keyword reports the nullability and the type
    # mismatch separately; both are true, both point at the same value.
    assert "/order/note: None for not nullable" in exc.errors
    assert {line.split(":")[0] for line in exc.errors} == {"/order/note"}


def test_a_required_read_only_property_is_still_required_on_a_response(world: World) -> None:
    """The response direction: ``readOnly`` marks a field the server writes and
    the client never sends, so on a *response* it is exactly the field that must
    be present. The direction-agnostic validator skips ``required`` for every
    readOnly property; the read validator does not. This is the keyword the
    first fixture omitted, and the one that silently disabled ``required`` on
    seven shipped schemas (adversarial review, konyklabs/roadmap#55)."""
    body = {"order": {key: value for key, value in GOOD_ORDER["order"].items() if key != "id"}}
    exc = _violation(world, "/v2/orders/ord_1", body)
    assert exc.errors == ("/order: 'id' is a required property",)


def test_null_where_nullable_passes(world: World) -> None:
    world.script.body = {"order": {**GOOD_ORDER["order"], "closed_at": None}}
    world.client.get("/v2/orders/ord_1")
    assert world.ledger.total("validated") == 1


def test_missing_required_names_the_object(world: World) -> None:
    order = dict(GOOD_ORDER["order"])
    del order["state"]
    exc = _violation(world, "/v2/orders/ord_1", {"order": order})
    assert exc.errors == ("/order: 'state' is a required property",)


def test_nested_ref_through_items_resolves(world: World) -> None:
    body = {"order": {**GOOD_ORDER["order"], "line_items": [{"quantity": "1"}, {"quantity": 2}]}}
    exc = _violation(world, "/v2/orders/ord_1", body)
    assert exc.errors == ("/order/line_items/1/quantity: 2 is not of type 'string'",)


def test_every_error_is_collected(world: World) -> None:
    body = {"order": {**GOOD_ORDER["order"], "version": "one", "state": "BOGUS"}}
    exc = _violation(world, "/v2/orders/ord_1", body)
    assert [line.split(":")[0] for line in exc.errors] == ["/order/state", "/order/version"]
    assert exc.body_excerpt.startswith('{"order"')


def test_violation_does_not_count_as_validated(world: World) -> None:
    _violation(world, "/v2/orders/ord_1", {"order": {"id": "x"}})
    assert world.ledger.rows() == ()


# ---------------------------------------------------------------------------
# Status fallback.
# ---------------------------------------------------------------------------


def test_envelope_fallback_validates_a_400(world: World) -> None:
    world.script.status = 400
    world.script.body = {"errors": [{"category": "INVALID_REQUEST_ERROR", "code": "MISSING_REQUIRED_PARAMETER"}]}
    res = world.client.post("/v2/orders", body={})
    assert res.status == 400
    assert world.ledger.row("POST /v2/orders") == LedgerRow("POST /v2/orders", validated=1)


def test_envelope_fallback_catches_a_bad_error_body(world: World) -> None:
    exc = _violation(
        world, "/v2/orders", {"errors": [{"category": "INVALID_REQUEST_ERROR"}]}, method="POST", status=400
    )
    assert exc.status == 400
    assert exc.errors == ("/errors/0: 'code' is a required property",)


def test_no_schema_for_status_is_a_violation(world: World) -> None:
    exc = _violation(world, "/v2/plain", {"anything": True})
    assert exc.errors == ("no schema for status 200",)
    assert "no schema for status 200" in str(exc)


def test_no_envelope_means_an_undeclared_status_is_a_violation() -> None:
    script = Script(status=400, body={"errors": []})
    unit = make_unit([route("POST", "/v2/orders", script.answer)])
    try:
        declaration = FidelityDeclaration(anchor="tests.synthetic", sources=DECLARATION.sources, error_envelope=None)
        client = ValidatingClient(unit, Surface(declaration, Extract(EXTRACT)))
        with pytest.raises(FidelityViolation, match="no schema for status 400"):
            client.post("/v2/orders", body={})
    finally:
        unit.stop()


# ---------------------------------------------------------------------------
# Bodies that are not JSON.
# ---------------------------------------------------------------------------


def test_text_body_is_skipped(world: World) -> None:
    world.script.reply = text("pong")
    world.client.get("/v2/plain")
    assert world.ledger.row("GET /v2/plain") == LedgerRow("GET /v2/plain", skipped_non_json=1)


def test_empty_body_is_skipped(world: World) -> None:
    world.script.reply = no_content()
    res = world.client.get("/v2/orders/ord_1")
    assert res.status == 204
    assert world.ledger.row("GET /v2/orders/{order_id}") == LedgerRow("GET /v2/orders/{order_id}", skipped_non_json=1)


def test_json_content_type_with_unparseable_body_is_a_violation(world: World) -> None:
    world.script.reply = ReplyInit(status=200, raw=b"{not json", headers={"content-type": "application/json"})
    with pytest.raises(FidelityViolation) as caught:
        world.client.get("/v2/orders/ord_1")
    assert caught.value.errors[0].startswith("(root): body is not JSON")
    assert caught.value.body_excerpt == "{not json"


def test_content_type_with_parameters_still_counts_as_json(world: World) -> None:
    world.script.reply = ReplyInit(
        status=200, raw=b'{"order": {"id": "x"}}', headers={"Content-Type": "application/json; charset=utf-8"}
    )
    with pytest.raises(FidelityViolation) as caught:
        world.client.get("/v2/orders/ord_1")
    assert caught.value.errors == ("/order: 'state' is a required property",)


# ---------------------------------------------------------------------------
# The other classifications.
# ---------------------------------------------------------------------------


def test_undeclared_route_raises(world: World) -> None:
    world.script.body = {"ok": True}
    with pytest.raises(UndeclaredRoute) as caught:
        world.client.get("/v2/surprise")
    exc = caught.value
    assert isinstance(exc, FidelityViolation)
    assert exc.route_key == "GET /v2/surprise"
    assert exc.operation_key is None
    assert "UNDECLARED" in str(exc)
    assert world.ledger.rows() == ()


def test_undeclared_route_is_counted_when_not_strict(lenient: World) -> None:
    lenient.script.body = {"ok": True}
    res = lenient.client.get("/v2/surprise")
    assert res.status == 200
    assert lenient.ledger.row("GET /v2/surprise") == LedgerRow("GET /v2/surprise", undeclared=1)


def test_excused_route_is_counted_not_validated(world: World) -> None:
    world.script.body = {"definitely": "not in any schema"}
    world.client.get("/v2/legacy/ping")
    assert world.ledger.row("GET /v2/legacy/ping") == LedgerRow("GET /v2/legacy/ping", excused=1)


def test_internal_route_is_counted_not_validated(world: World) -> None:
    res = world.client.get("/__unit/info")
    assert res.status == 200
    assert world.ledger.row("GET /__unit/info") == LedgerRow("GET /__unit/info", internal=1)


def test_unmatched_requests_are_counted_under_the_request_key(world: World) -> None:
    assert world.client.get("/v2/nothing/here").status == 404
    assert world.client.post("/v2/legacy/ping").status == 405
    assert world.ledger.rows() == (
        LedgerRow("GET /v2/nothing/here", unmatched=1),
        LedgerRow("POST /v2/legacy/ping", unmatched=1),
    )


# ---------------------------------------------------------------------------
# Aliases.
# ---------------------------------------------------------------------------


def test_alias_route_validates_against_the_aliased_operation(world: World) -> None:
    world.script.body = {"merchant": {"id": "m_1", "country": "US"}}
    world.client.get("/v2/merchants/me")
    exc = _violation(world, "/v2/merchants/me", {"merchant": {"country": "US"}})
    assert exc.route_key == "GET /v2/merchants/me"
    assert exc.operation_key == "GET /v2/merchants/{merchant_id}"
    assert exc.errors == ("/merchant: 'id' is a required property",)


def test_ledger_keys_the_alias_route_by_its_own_spelling(world: World) -> None:
    world.script.body = {"merchant": {"id": "m_1"}}
    world.client.get("/v2/merchants/me")
    assert world.ledger.rows() == (LedgerRow("GET /v2/merchants/me", validated=1),)


# ---------------------------------------------------------------------------
# The ledger.
# ---------------------------------------------------------------------------


def test_ledger_totals_are_exact(lenient: World) -> None:
    lenient.script.body = GOOD_ORDER
    lenient.client.get("/v2/orders/ord_1")
    lenient.client.get("/v2/orders/ord_2")
    lenient.script.body = {"merchant": {"id": "m_1"}}
    lenient.client.get("/v2/merchants/me")
    lenient.client.get("/v2/legacy/ping")
    lenient.client.get("/v2/surprise")
    lenient.client.get("/__unit/info")
    lenient.client.get("/v2/nothing")
    lenient.script.reply = text("pong")
    lenient.client.get("/v2/plain")
    assert lenient.ledger.rows() == (
        LedgerRow("GET /__unit/info", internal=1),
        LedgerRow("GET /v2/legacy/ping", excused=1),
        LedgerRow("GET /v2/merchants/me", validated=1),
        LedgerRow("GET /v2/nothing", unmatched=1),
        LedgerRow("GET /v2/orders/{order_id}", validated=2),
        LedgerRow("GET /v2/plain", skipped_non_json=1),
        LedgerRow("GET /v2/surprise", undeclared=1),
    )
    assert lenient.ledger.total("validated") == 3
    assert lenient.ledger.summary() == (
        "fidelity: 3 validated, 0 deviated, 1 excused, 1 internal, 1 undeclared, 0 undeclared status, "
        "1 unmatched, 1 skipped non json, 0 request validated, 0 request deviated over 7 routes"
    )


def test_ledger_rejects_an_unknown_counter() -> None:
    with pytest.raises(ValueError, match="unknown ledger counter"):
        Ledger().record("GET /x", "bogus")  # type: ignore[arg-type]


def test_ledger_is_shared_across_clients(world: World) -> None:
    world.script.body = GOOD_ORDER
    second = ValidatingClient(world.unit, world.client.surface, world.ledger)
    world.client.get("/v2/orders/ord_1")
    second.get("/v2/orders/ord_1")
    assert world.ledger.row("GET /v2/orders/{order_id}").validated == 2
    assert world.client.ledger is second.ledger


# ---------------------------------------------------------------------------
# Caching.
# ---------------------------------------------------------------------------


def test_validators_are_built_once_per_operation_and_status(world: World) -> None:
    world.script.body = GOOD_ORDER
    world.client.get("/v2/orders/ord_1")
    world.client.get("/v2/orders/ord_2")
    assert world.client.built == 1
    world.script.status = 400
    world.script.body = {"errors": [{"category": "A", "code": "B"}]}
    world.client.get("/v2/orders/ord_3")
    assert world.client.built == 2
    world.client.get("/v2/orders/ord_4")
    assert world.client.built == 2


def test_default_ledger_is_created_when_none_is_given(world: World) -> None:
    client = ValidatingClient(world.unit, world.client.surface)
    world.script.body = GOOD_ORDER
    client.get("/v2/orders/ord_1")
    assert client.ledger.total("validated") == 1
    assert client.ledger is not world.ledger


# -- deviations -------------------------------------------------------------


def _deviating(deviation: Deviation) -> World:
    """The synthetic world with one declared deviation."""
    script = Script()
    unit = make_unit([route("GET", "/v2/orders/{order_id}", script.answer)], control_routes=control_plane_routes)
    ledger = Ledger()
    declaration = replace(DECLARATION, deviations=(deviation,))
    return World(unit, script, ledger, ValidatingClient(unit, Surface(declaration, Extract(EXTRACT)), ledger))


BOGUS_STATE_DEVIATION = Deviation(
    pointer="/order/state",
    keyword="enum",
    value="BOGUS",
    reason="the vendor names BOGUS in prose and omits it from the enumeration",
    url="https://example.invalid/prose",
)


def test_a_declared_deviation_excuses_exactly_that_error() -> None:
    world = _deviating(BOGUS_STATE_DEVIATION)
    world.script.body = {"order": {**GOOD_ORDER["order"], "state": "BOGUS"}}
    world.client.get("/v2/orders/ord_1")
    assert world.ledger.total("validated") == 1
    assert world.ledger.total("deviated") == 1
    assert "1 deviated" in world.ledger.summary()


def test_a_deviation_is_narrow_on_value_and_pointer() -> None:
    world = _deviating(BOGUS_STATE_DEVIATION)
    # Same keyword and pointer, a different value: still a violation.
    exc = _violation(world, "/v2/orders/ord_1", {"order": {**GOOD_ORDER["order"], "state": "OTHER"}})
    assert exc.errors[0].startswith("/order/state: 'OTHER' is not one of")
    # Same value, a different keyword (a type error, not an enum error): still a violation.
    world = _deviating(replace(BOGUS_STATE_DEVIATION, keyword="type"))
    exc = _violation(world, "/v2/orders/ord_1", {"order": {**GOOD_ORDER["order"], "state": "BOGUS"}})
    assert len(exc.errors) == 1
    assert world.ledger.total("deviated") == 0


def test_a_deviation_pointer_wildcard_matches_one_segment() -> None:
    wide = replace(BOGUS_STATE_DEVIATION, pointer="/order/*")
    assert wide.matches(keyword="enum", pointer="/order/state", instance="BOGUS")
    assert not wide.matches(keyword="enum", pointer="/order/line_items/0/state", instance="BOGUS")
    assert not wide.matches(keyword="enum", pointer="/state", instance="BOGUS")


# -- routing agreement -------------------------------------------------------


def test_a_query_string_in_the_path_is_matched_the_way_the_kernel_matches_it(world: World) -> None:
    """``make_request`` splits ``?...`` off before routing; the wrapper must
    match the same bare path, or a routed 200 would be counted ``unmatched``
    and returned unvalidated (adversarial review, konyklabs/roadmap#55)."""
    world.script.body = GOOD_ORDER
    world.client.get("/v2/orders/ord_1?fields=all")
    assert world.ledger.row("GET /v2/orders/{order_id}").validated == 1
    assert world.ledger.total("unmatched") == 0


def test_a_success_nothing_routed_is_a_validator_defect_not_a_vendor_fact(world: World) -> None:
    world.script.body = GOOD_ORDER
    world.client.validator._router = Router([])
    with pytest.raises(RuntimeError, match="matched no route in the validator"):
        world.client.get("/v2/orders/ord_1")


# -- the envelope must still carry the error member -------------------------


def test_an_error_status_through_the_envelope_must_carry_the_error_member(world: World) -> None:
    """Deep-lens finding D1 (konyklabs/roadmap#55): the success schema requires
    nothing, so a 404 answering a success payload validated. Not any more."""
    exc = _violation(world, "/v2/orders/ord_1", GOOD_ORDER, status=404)
    assert exc.errors == (
        "/errors: status 404 answered through the 200 envelope must carry a non-empty 'errors' "
        "(the success schema requires nothing)",
    )
    exc = _violation(world, "/v2/orders/ord_1", {"errors": []}, status=404)
    assert exc.errors[0].startswith("/errors: status 404")
    world.script.body = {"errors": [{"category": "INVALID_REQUEST_ERROR", "code": "NOT_FOUND"}]}
    world.script.status = 404
    world.client.get("/v2/orders/ord_1")
    assert world.ledger.total("validated") == 1


def test_a_deviation_scoped_to_routes_does_not_carry_elsewhere() -> None:
    scoped = replace(BOGUS_STATE_DEVIATION, routes=("POST /v2/orders",))
    world = _deviating(scoped)
    exc = _violation(world, "/v2/orders/ord_1", {"order": {**GOOD_ORDER["order"], "state": "BOGUS"}})
    assert exc.errors[0].startswith("/order/state: 'BOGUS'")
    assert world.ledger.absorbed() == ()


def test_the_ledger_names_which_deviation_absorbed_what() -> None:
    world = _deviating(BOGUS_STATE_DEVIATION)
    world.script.body = {"order": {**GOOD_ORDER["order"], "state": "BOGUS"}}
    world.client.get("/v2/orders/ord_1")
    world.client.get("/v2/orders/ord_1")
    assert world.ledger.absorbed() == ((BOGUS_STATE_DEVIATION.label, 2),)
    assert BOGUS_STATE_DEVIATION.label == 'enum /order/state = "BOGUS"'


def test_a_null_where_the_spec_pairs_a_reference_with_nullable_passes(world: World) -> None:
    """The cut form of ``{"$ref": X, "nullable": true}``; the reference's own
    error surfaces for a wrong shape, not the anyOf's generic one."""
    schema = world.client.surface.extract.schemas["Order"]["properties"]
    schema["audit"] = {"anyOf": [{"$ref": "#/components/schemas/LineItem"}, {"enum": [None]}]}  # type: ignore[index]
    world.script.body = {"order": {**GOOD_ORDER["order"], "audit": None}}
    world.client.get("/v2/orders/ord_1")
    assert world.ledger.total("validated") == 1
    exc = _violation(world, "/v2/orders/ord_1", {"order": {**GOOD_ORDER["order"], "audit": {"quantity": 3}}})
    assert (
        exc.errors[0].startswith("/order/audit/quantity: 3 is not of type 'string'") or "/order/audit" in exc.errors[0]
    )


# -- overrides ---------------------------------------------------------------


def test_an_override_validates_against_the_named_component_for_that_route_and_status() -> None:
    """The vendor's guide documents a shape its spec does not declare for one
    route; the declaration names the component to use instead."""
    script = Script()
    unit = make_unit([route("GET", "/v2/orders/{order_id}", script.answer)], control_routes=control_plane_routes)
    ledger = Ledger()
    override = Override(
        route="GET /v2/orders/{order_id}",
        status=200,
        schema="LineItem",
        reason="the guide documents a bare line item here",
        url="https://example.invalid/guide",
    )
    # No envelope here, so a status the override does not name has no schema.
    declaration = replace(DECLARATION, overrides=(override,), error_envelope=None, error_member=None)
    client = ValidatingClient(unit, Surface(declaration, Extract(EXTRACT)), ledger)
    script.body = {"quantity": "2"}  # a LineItem, not an order
    client.get("/v2/orders/ord_1")
    assert ledger.total("validated") == 1
    script.body = {"quantity": 2}
    with pytest.raises(FidelityViolation) as caught:
        client.get("/v2/orders/ord_1")
    assert caught.value.errors == ("/quantity: 2 is not of type 'string'",)
    # Another status on the same route is untouched by the override.
    script.body = GOOD_ORDER
    script.status = 201
    with pytest.raises(FidelityViolation, match="no schema for status 201"):
        client.get("/v2/orders/ord_1")


# -- statuses the document never declares ----------------------------------


def test_an_undeclared_error_status_is_checked_against_the_error_schema_and_counted() -> None:
    """Deep-lens D2 (konyklabs/roadmap#56): the error-schema fallback must not
    make an invented status look declared. Shape checked, judgment counted."""
    script = Script()
    unit = make_unit([route("GET", "/v2/orders/{order_id}", script.answer)], control_routes=control_plane_routes)
    ledger = Ledger()
    extract = {**EXTRACT, "components": {"schemas": {**EXTRACT["components"]["schemas"], "Err": {"type": "object"}}}}
    declaration = replace(DECLARATION, error_envelope=None, error_member=None, error_schema="Err")
    client = ValidatingClient(unit, Surface(declaration, Extract(extract)), ledger)
    script.body = {"message": "no"}
    script.status = 418
    client.get("/v2/orders/ord_1")
    assert ledger.row("GET /v2/orders/{order_id}").validated == 1
    assert ledger.row("GET /v2/orders/{order_id}").undeclared_status == 1
    assert "1 undeclared status" in ledger.summary()
    script.body = ["not", "an", "object"]
    with pytest.raises(FidelityViolation):
        client.get("/v2/orders/ord_1")


def test_validators_are_cached_per_unit_route_not_per_operation() -> None:
    """Deep-lens D3: an alias maps two unit routes onto one operation and an
    override is per route, so the cache must not let the first route's
    validator answer for the second."""
    script = Script()
    unit = make_unit(
        [route("GET", "/v2/merchants/me", script.answer), route("GET", "/v2/merchants/{merchant_id}", script.answer)],
        control_routes=control_plane_routes,
    )
    ledger = Ledger()
    override = Override(
        route="GET /v2/merchants/me", status=200, schema="LineItem", reason="the guide", url="https://example.invalid/"
    )
    declaration = replace(DECLARATION, overrides=(override,), error_envelope=None, error_member=None)
    client = ValidatingClient(unit, Surface(declaration, Extract(EXTRACT)), ledger)
    script.body = {"merchant": {"id": "m1"}}
    client.get("/v2/merchants/m1")  # the plain route: the operation's own schema
    with pytest.raises(FidelityViolation, match="quantity"):
        client.get("/v2/merchants/me")  # the alias route: the override's LineItem schema, not the cached one


# ---------------------------------------------------------------------------
# Request bodies (F3): what the unit ACCEPTED, against the vendor's own schema.
# ---------------------------------------------------------------------------

GOOD_CREATE = {"idempotency_key": "k-1", "note": "a note"}
"""A request body ``CreateOrderRequest`` accepts."""


@pytest.fixture
def requesting() -> Any:
    """The synthetic world with request validation on."""
    built = _build(validate_requests=True)
    try:
        yield built
    finally:
        built.unit.stop()


def test_a_schema_violating_body_the_unit_accepted_is_a_violation(requesting: World) -> None:
    """The defect F3 names: the fake is more permissive than the API it stands
    in for, so a consumer's test passes here and its request fails in production.
    Nothing in the response can show that -- the answer is a well-formed 200."""
    requesting.script.body = GOOD_ORDER
    with pytest.raises(FidelityViolation) as caught:
        requesting.client.post("/v2/orders", {"idempotency_key": 7})
    exc = caught.value
    assert exc.subject == "request body"
    assert "request body" in str(exc)
    assert exc.errors == ("/idempotency_key: 7 is not of type 'string'",)
    # The answer itself was fine; nothing was recorded against the response.
    assert requesting.ledger.total("request_validated") == 0
    assert requesting.ledger.total("validated") == 0


def test_a_missing_required_member_of_the_request_body_is_a_violation(requesting: World) -> None:
    requesting.script.body = GOOD_ORDER
    with pytest.raises(FidelityViolation, match="request body") as caught:
        requesting.client.post("/v2/orders", {"note": "no key"})
    assert caught.value.errors == ("(root): 'idempotency_key' is a required property",)


def test_a_request_the_unit_refused_is_not_a_fidelity_question(requesting: World) -> None:
    """A 4xx means the unit and the vendor's schema agree the body is wrong. Only
    an *accepted* body is evidence about the fake's permissiveness."""
    requesting.script.status = 400
    requesting.script.body = {"errors": [{"category": "INVALID_REQUEST_ERROR", "code": "BAD"}]}
    requesting.client.post("/v2/orders", {"idempotency_key": 7})
    assert requesting.ledger.total("request_validated") == 0
    assert requesting.ledger.row("POST /v2/orders").validated == 1


def test_the_flag_off_validates_no_request(world: World) -> None:
    world.script.body = GOOD_ORDER
    world.client.post("/v2/orders", {"idempotency_key": 7})
    assert world.ledger.total("request_validated") == 0
    assert world.ledger.row("POST /v2/orders").validated == 1


def test_an_accepted_body_that_matches_is_counted(requesting: World) -> None:
    requesting.script.body = GOOD_ORDER
    requesting.client.post("/v2/orders", GOOD_CREATE)
    row = requesting.ledger.row("POST /v2/orders")
    assert (row.request_validated, row.request_deviated, row.validated) == (1, 0, 1)
    assert "1 request validated, 0 request deviated" in requesting.ledger.summary()


def test_a_route_with_no_request_body_schema_is_not_request_validated(requesting: World) -> None:
    """``GET /v2/orders/{id}`` declares no ``requestBody``; there is nothing to
    check and nothing to count, whatever a caller sends."""
    requesting.script.body = GOOD_ORDER
    requesting.client.get("/v2/orders/ord_1")
    assert requesting.ledger.total("request_validated") == 0


def test_a_body_sent_under_another_media_type_is_not_checked(requesting: World) -> None:
    """The schema describes ``application/json``; a form body is a different
    document the operation may or may not declare, and never this one."""
    requesting.script.body = GOOD_ORDER
    requesting.client.post(
        "/v2/orders", raw_body="idempotency_key=7", headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert requesting.ledger.total("request_validated") == 0


def test_a_raw_body_that_is_not_json_names_the_request(requesting: World) -> None:
    requesting.script.body = GOOD_ORDER
    with pytest.raises(FidelityViolation, match="body is not JSON") as caught:
        requesting.client.post("/v2/orders", raw_body=b"{oops", headers={"content-type": "application/json"})
    assert caught.value.subject == "request body"


def test_an_empty_body_is_nothing_to_validate(requesting: World) -> None:
    """``requestBody.required`` is a statement about the request the unit should
    have refused, and the unit's own refusal is what states it; an absent body
    has no JSON document to check against the schema."""
    requesting.script.body = GOOD_ORDER
    requesting.client.post("/v2/orders")
    assert requesting.ledger.total("request_validated") == 0


def test_a_declared_deviation_absorbs_a_request_body_error() -> None:
    """The same mechanism as a response deviation, and the same format: a
    deviation names a pointer and a keyword, which are unambiguous already."""
    script = Script()
    unit = make_unit([route("POST", "/v2/orders", script.answer)], control_routes=control_plane_routes)
    ledger = Ledger()
    deviation = Deviation(
        pointer="/state",
        keyword="enum",
        value="BOGUS",
        reason="the vendor's guide names BOGUS and its spec omits it",
        url="https://example.invalid/prose",
    )
    declaration = replace(DECLARATION, deviations=(deviation,))
    client = ValidatingClient(unit, Surface(declaration, Extract(EXTRACT)), ledger, validate_requests=True)
    try:
        script.body = GOOD_ORDER
        client.post("/v2/orders", {**GOOD_CREATE, "state": "BOGUS"})
        assert ledger.total("request_validated") == 1
        assert ledger.total("request_deviated") == 1
        assert ledger.absorbed() == ((deviation.label, 1),)
    finally:
        unit.stop()
