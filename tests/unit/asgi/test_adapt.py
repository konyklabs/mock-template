"""The translation between an ASGI request and a ``UnitRequest``.

Each test here pins one normalisation that a second binding would get subtly
wrong, and every one of them is observable only from inside the core -- which
is why they are asserted through a route that reports what it was given rather
than by calling the converter with a hand-built scope.
"""

from __future__ import annotations

from typing import Any

import anyio
import httpx
import pytest
from starlette.requests import Request

from vendorfake.asgi.adapt import TRANSPORT, request_path, to_response
from vendorfake.core.kernel.types import UnitResponse


def call(app: Any, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One request through the ASGI transport, run on its own event loop."""

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://unit.test") as client:
            return await client.request(method, url, **kwargs)

    return anyio.run(run)


def test_transport_is_named_http(app: Any) -> None:
    """``UnitRequest.transport`` says which binding carried the call.

    A conformance check compares two bindings' answers to the same scenario;
    it can only do that if each one identifies itself.
    """
    body = call(app, "GET", "/v2/orders/abc").json()
    assert body["transport"] == TRANSPORT == "http"


def test_path_keeps_percent_escapes(app: Any) -> None:
    """``%2F`` inside a segment must not become a segment separator.

    ``scope["path"]`` is already decoded by the server, so reading it would
    turn ``/v2/orders/a%2Fb`` into three segments and route it to nothing.
    ``raw_path`` is what preserves the consumer's intent, and the router does
    its own per-segment decode afterwards.
    """
    body = call(app, "GET", "/v2/orders/a%2Fb").json()
    assert body["path"] == "/v2/orders/a%2Fb"
    assert body["order_id"] == "a/b"


def test_repeated_headers_are_joined(app: Any) -> None:
    """Two headers with the same name arrive as one field, comma-joined.

    ``dict(request.headers)`` keeps only the first value, silently. RFC 9110
    says a repeated field is equivalent to the joined one, and that is also
    what the reference sees, because Node's http module joins before the
    handler is reached.
    """
    body = call(app, "GET", "/v2/orders/abc", headers=[("x-dup", "one"), ("x-dup", "two")]).json()
    assert body["headers"]["x-dup"] == "one, two"


def test_header_names_are_lowercased(app: Any) -> None:
    body = call(app, "GET", "/v2/orders/abc", headers={"X-Mixed-Case": "v"}).json()
    assert "x-mixed-case" in body["headers"]
    assert body["headers"]["x-mixed-case"] == "v"


def test_repeated_query_parameters_keep_every_value_and_the_scalar_view_keeps_the_last(app: Any) -> None:
    """``UnitRequest.query`` is ``str -> str`` in every binding and
    ``query_all`` is ``str -> Sequence[str]`` in every binding.

    Starlette's ``QueryParams`` is multi-valued; letting it through would give
    the HTTP binding a list where the in-process and file-drop bindings have a
    string. ``dict(query_params)`` would instead drop ``x=1`` on the floor.
    """
    body = call(app, "GET", "/v2/orders/abc?x=1&x=2&y=3").json()
    assert body["query"] == {"x": "2", "y": "3"}
    assert body["query_all"] == {"x": ["1", "2"], "y": ["3"]}


def test_a_bare_query_key_is_kept_as_an_empty_value(app: Any) -> None:
    body = call(app, "GET", "/v2/orders/abc?flag&x=1").json()
    assert body["query"] == {"flag": "", "x": "1"}
    assert body["query_all"] == {"flag": [""], "x": ["1"]}


def test_a_raw_path_that_still_carries_the_query_string_is_cut_at_the_question_mark() -> None:
    """ASGI says ``raw_path`` excludes the query, but a server that leaves it on
    would otherwise feed every parameter to ``make_request`` twice -- once from
    the path, once from ``query_string``."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v2/orders/a/b",
        "raw_path": b"/v2/orders/a%2Fb?x=1&x=2",
        "query_string": b"x=1&x=2",
        "headers": [],
    }
    assert request_path(Request(scope)) == "/v2/orders/a%2Fb"


def test_body_reaches_the_core_as_the_exact_bytes(app: Any) -> None:
    """No re-encoding anywhere between the socket and ``raw_body``.

    A multi-byte character makes the difference visible: a body re-encoded via
    a parsed intermediate would very likely still be valid and still be a
    different length, which is exactly the failure a signature check reports
    hours later as "signature mismatch".
    """
    payload = "smørrebrød".encode()
    body = call(app, "POST", "/v2/orders/abc", content=payload).json()
    assert body["raw_len"] == len(payload) == 12


def test_empty_body_is_empty_not_absent(app: Any) -> None:
    body = call(app, "POST", "/v2/orders/abc").json()
    assert body["raw_len"] == 0


def test_inbound_request_id_is_honoured(app: Any) -> None:
    """The correlation id crosses the transport rather than being minted at it.

    A caller that stamps ``x-unit-request-id`` is correlating this call with
    something in their own logs; minting a fresh one would break exactly the
    thing the header exists for.
    """
    response = call(app, "GET", "/v2/orders/abc", headers={"x-unit-request-id": "req-fixed-1"})
    assert response.headers["x-unit-request-id"] == "req-fixed-1"


def test_to_response_passes_bytes_through_untouched() -> None:
    """The converter is not allowed to re-render anything.

    Asserted directly, with bytes that are deliberately not valid JSON and not
    valid UTF-8: anything that tried to parse or re-encode this would raise
    rather than pass it on, so the test fails loudly instead of subtly.
    """
    raw = b"\xff\xfe not json at all"
    response = to_response(UnitResponse(status=418, headers={"x-unit-error": "teapot"}, body=raw))
    assert response.body == raw
    assert response.status_code == 418
    assert response.headers["x-unit-error"] == "teapot"


@pytest.mark.parametrize("path", ["/v2/plain", "/__unit/health"])
def test_content_type_is_the_unit_s_own(app: Any, path: str) -> None:
    """Starlette must not substitute a content type of its own.

    ``Response`` defaults to no media type only when the caller supplied one in
    ``headers``; a ``PlainTextResponse`` or a ``JSONResponse`` would overwrite
    what the core's ``normalize()`` decided, and the core's choice is the one
    the conformance suite compares across bindings.
    """
    expected = {"/v2/plain": "text/plain; charset=utf-8", "/__unit/health": "application/json"}[path]
    assert call(app, "GET", path).headers["content-type"] == expected
