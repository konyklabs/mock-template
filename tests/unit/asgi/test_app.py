"""The application's shape: one catch-all, no framework answers, no middleware.

These are the tests that hold D-002's invariant in place at the one seam where
a web framework is present. Most of them assert an *absence* -- no 404 of
Starlette's, no 422, no middleware, no framework-generated document -- which is
why each one names the thing that would be there if the property broke.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.asgi.test_adapt import call
from vendorfake.asgi import HTTP_METHODS, OPENAPI_PATH, FrameworkTripwire, create_app, registered_methods
from vendorfake.core.transport.inprocess import in_process

EXPECTED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"})


def test_the_verb_set_is_complete(app: Any) -> None:
    """Every HTTP verb reaches the unit; none is left for Starlette to refuse.

    Read back off the built application rather than off the constant, because
    the constant is what we meant and this is what the framework did with it.
    A verb missing here is answered by Starlette with a 405 that never reaches
    the unit -- a hole in something that still looks like a catch-all.
    """
    assert set(HTTP_METHODS) == EXPECTED_METHODS
    assert registered_methods(app) == EXPECTED_METHODS


def test_there_is_exactly_one_catch_all_route(app: Any) -> None:
    """One route, one wildcard path, no typed parameters.

    A second route is how a framework 405 gets back in: the moment a concrete
    path is registered, a request to it with an unlisted method is Starlette's
    to answer rather than the unit's.
    """
    paths = [getattr(route, "path", None) for route in app.routes]
    assert paths == ["/{full_path:path}"]


def test_an_unknown_path_gets_the_vendor_s_404(app: Any) -> None:
    """Not ``{"detail": "Not Found"}``.

    A consumer testing their own error handling against this fake must see the
    vendor's error envelope. Starlette's document would teach them a shape the
    real API never sends.
    """
    response = call(app, "GET", "/no/such/path")
    assert response.status_code == 404
    body = response.json()
    assert "detail" not in body
    assert body == {"error": {"code": "no_route", "path": "/no/such/path"}}


def test_a_wrong_verb_gets_the_vendor_s_405_with_the_allowed_list(app: Any) -> None:
    response = call(app, "DELETE", "/v2/orders/abc")
    assert response.status_code == 405
    assert response.headers["x-unit-error"] == "method_not_allowed"
    assert response.json()["error"]["info"]["allowed"] == ["GET", "POST"]


def test_a_malformed_json_body_is_400_and_never_422(app: Any) -> None:
    """422 is the framework's signature. Its absence is the invariant.

    A 422 could only come from FastAPI having parsed and validated a body,
    which is the leak the core exists to prevent. The core's own reader answers
    ``invalid_json``, which the vendor shapes as a 400.
    """
    response = call(
        app,
        "POST",
        "/__unit/echo",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.headers["x-unit-error"] == "invalid_json"


def test_a_form_encoded_body_is_understood_over_the_transport(app: Any) -> None:
    """The shape that broke two of three earlier implementations.

    ``python-multipart`` is not installed, so a ``Form(...)`` parameter would
    have raised at import and ``await request.form()`` would raise here. This
    passing means the adapter read bytes and the core parsed them -- and the
    ``fields_multi`` view proves the core kept what a single ``str -> str``
    view would have thrown away.

    Fidelity note: form encoding on an OAuth token endpoint is a judgment call
    in the consumer's favour, not documented vendor behaviour. This is a
    harness test, and it is deliberately run against the vendor-neutral echo
    route so that it states nothing about any vendor at all.
    """
    response = call(
        app,
        "POST",
        "/__unit/echo",
        content=b"grant_type=authorization_code&code=abc&scope=one&scope=two",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content_type"] == "application/x-www-form-urlencoded"
    assert body["fields"] == {"grant_type": "authorization_code", "code": "abc", "scope": "two"}
    assert body["fields_multi"]["scope"] == ["one", "two"]


def test_no_middleware_is_installed(app: Any) -> None:
    """Not compression, not CORS, not a request id.

    Every middleware is a chance to rewrite the bytes the unit produced, and
    byte-for-byte agreement between bindings is a conformance contract. The
    framework's own exception middleware is the exception, in both senses: it
    is what routes to the tripwire handlers below.
    """
    installed = [entry.cls.__name__ for entry in app.user_middleware]
    assert installed == []


@pytest.mark.parametrize(
    "path",
    ["/__unit/health", "/__unit/info", "/__unit/routes", "/__unit/errors", "/v2/stable", "/v2/plain", "/no/such/path"],
)
def test_http_and_in_process_bindings_agree_byte_for_byte(app: Any, unit: Any, path: str) -> None:
    """The same request, two bindings, identical status and identical bytes.

    Excluded from the header comparison, and named rather than hand-waved:
    ``x-unit-request-id`` is minted per binding and can never match;
    ``content-length`` is added by Starlette. Over a socket a server adds
    ``date``, ``server`` and ``connection`` as well, which is why the
    out-of-process test carries the same exclusion list.

    ``/__unit/info`` is in the list on purpose: it is the largest body the unit
    produces and the one most likely to expose a re-serialisation, since a
    round trip through a parser would reorder or re-space it.
    """
    excluded = {"x-unit-request-id", "content-length", "date", "server", "connection"}

    over_http = call(app, "GET", path)
    in_process_response = in_process(unit).get(path)

    assert over_http.status_code == in_process_response.status
    if path in {"/__unit/health", "/__unit/info"}:
        # Two of the fields these routes report are readings of the wall clock
        # -- how long the unit has been up, and what time it is now -- so they
        # move between two calls by construction. Normalising exactly those two,
        # rather than dropping the routes, keeps the largest body the unit
        # produces inside the comparison: it is the one most likely to expose a
        # re-serialisation, and dropping it to avoid a flake would be trading
        # the assertion for the convenience.
        left = json.loads(over_http.content)
        right = in_process_response.json()
        for side in (left, right):
            side.pop("uptime_ms", None)
            if "clock" in side:
                side["clock"].pop("now", None)
        assert left == right
    else:
        assert over_http.content == in_process_response.body

    http_headers = {k: v for k, v in over_http.headers.items() if k not in excluded}
    unit_headers = {k: v for k, v in in_process_response.headers.items() if k not in excluded}
    assert http_headers == unit_headers


def test_the_vendor_decorate_hook_survives_the_transport(app: Any) -> None:
    """A header the vendor added in the core is present on the wire.

    The alternative design -- setting it in middleware at the edge, which the
    fidelity audit suggested -- would give it to the HTTP binding only, so the
    in-process and file-drop bindings would silently lack it.
    """
    assert call(app, "GET", "/v2/orders/abc").headers["acme-version"] == "2024-01-01"


# ---------------------------------------------------------------------------
# The tripwire.
# ---------------------------------------------------------------------------


def test_framework_answered_is_zero_after_ordinary_traffic(app: Any, tripwire: FrameworkTripwire) -> None:
    """The number the whole design is trying to keep at zero.

    Read over the wire rather than off the object, because over the wire is the
    only place a parent process can read it -- and reading it the same way here
    means the out-of-process test is asserting the same thing this one is.
    """
    for method, path in (
        ("GET", "/v2/orders/abc"),
        ("POST", "/v2/orders/abc"),
        ("GET", "/nope"),
        ("HEAD", "/v2/plain"),
    ):
        call(app, method, path)
    assert tripwire.count == 0, tripwire.recent
    assert call(app, "GET", "/__unit/health").json()["framework_answered"] == 0


def test_an_exotic_verb_trips_the_wire_and_still_gets_the_vendor_s_answer(
    app: Any, tripwire: FrameworkTripwire
) -> None:
    """A method outside the registered set is the one hole left, by design.

    Starlette raises its 405 before the catch-all is reached. The handler
    records that it happened and then dispatches to the unit anyway, so the
    consumer still gets a vendor-shaped body and the counter -- not the
    response -- is where the hole is reported. Both halves are asserted,
    because a fix that silenced the counter would look like a pass.
    """
    response = call(app, "PROPFIND", "/no/such/path")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "no_route", "path": "/no/such/path"}}
    assert tripwire.count == 1
    assert "PROPFIND" in tripwire.recent[0]
    assert call(app, "GET", "/__unit/health").json()["framework_answered"] == 1


def test_a_unit_built_without_a_tripwire_reports_zero(unit: Any) -> None:
    """The default is the true answer, not a stub.

    With no framework in front of the unit, nothing could have answered ahead
    of it, so 0 is a fact rather than a placeholder -- which matters because
    the in-process conformance run asserts the same field.
    """
    assert in_process(unit).get("/__unit/health").json()["framework_answered"] == 0


def test_the_tripwire_bounds_what_it_remembers(app: Any) -> None:
    """A counter, plus a bounded sample. Not an unbounded log.

    A long-running server that recorded every hit would leak; and after the
    first hit the invariant is already broken, so the sample only has to be
    big enough to say what happened.
    """
    wire = FrameworkTripwire(limit=2)
    for index in range(5):
        wire.record(f"hit {index}")
    assert wire.count == 5
    assert wire.recent == ["hit 0", "hit 1"]


# ---------------------------------------------------------------------------
# The generated document.
# ---------------------------------------------------------------------------


def test_the_framework_s_own_openapi_endpoints_are_off(app: Any) -> None:
    """``/openapi.json`` and ``/docs`` belong to the unit, not to FastAPI.

    With a single catch-all route the framework's generator can only describe a
    wildcard, and a wrong description served at the conventional path is worse
    than none. Both now reach the unit, which does not know them, and get the
    vendor's 404.
    """
    assert app.openapi_url is None
    assert app.docs_url is None
    for path in ("/openapi.json", "/docs", "/redoc"):
        response = call(app, "GET", path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "no_route"


def test_the_generated_document_is_served_and_describes_the_real_routes(app: Any) -> None:
    response = call(app, "GET", OPENAPI_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    document = response.json()
    assert document["openapi"] == "3.1.0"
    assert "/v2/orders/{order_id}" in document["paths"]
    assert set(document["paths"]["/v2/orders/{order_id}"]) == {"get", "post"}
    assert document["paths"]["/__unit/health"]["get"]["operationId"] == "UnitHealth"


def test_the_document_path_is_only_a_document_path(app: Any) -> None:
    """``GET`` and ``HEAD`` only; anything else falls through to the unit.

    Registering it as a route of its own would have handed Starlette a
    concrete path, and with it the right to answer 405 on that path. Handling
    it inside the catch-all keeps every other method the unit's business --
    and the unit has no such route, so it 404s, which is the honest answer.
    """
    assert call(app, "GET", OPENAPI_PATH).status_code == 200
    assert call(app, "HEAD", OPENAPI_PATH).status_code == 200
    posted = call(app, "POST", OPENAPI_PATH)
    assert posted.status_code == 404
    assert posted.json()["error"]["code"] == "no_route"


def test_create_app_is_a_factory(unit: Any) -> None:
    """Two calls build two applications, and neither is a module global.

    A module-level ``app = FastAPI()`` would be constructed on import and would
    need a unit before anyone asked for one -- which means a global unit, which
    means one test's state reaching another's.
    """
    import vendorfake.asgi.app as module

    assert not hasattr(module, "app")
    first = create_app(unit)
    second = create_app(unit)
    assert first is not second
