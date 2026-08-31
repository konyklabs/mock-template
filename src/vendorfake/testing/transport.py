"""An ``httpx`` transport that hands each request straight to a unit.

FOR: letting a consumer's test drive a unit with the client they already use
-- ``httpx.Client`` -- without a socket, a port or an event loop. The consumer
writes ``client.post("/v2/orders", json=...)`` exactly as they would against a
served URL, and the request goes to :meth:`Unit.handle` in this thread.

INVARIANT: **the bytes are the unit's bytes.** The request body is read once
and handed over untouched as ``raw_body``; the response body is the unit's
``UnitResponse.body`` placed into an ``httpx.Response`` without re-encoding.

INVARIANT: **the request is normalised the way the ASGI adapter normalises
it**, so that a test which is green here is green against the served unit and
the container. This is a third binding -- the conformance suite's C10 compares
the ``InProcessClient`` with HTTP, and that client's ``Mapping[str, str]``
headers cannot even express a repeated header -- so parity is pinned by a
test of its own (``tests/unit/testing``) that sends the same request through
this transport and through a real server and compares the echoed bytes. The
one normalisation with room to differ is repeated headers:
``vendorfake.asgi.adapt.request_headers`` joins them with ``", "``, and so
does :func:`_headers` below. It is mirrored rather than imported because the
adapter's helper takes a Starlette ``Request``, which an ``httpx`` transport
does not have.

WHY NOT ``httpx.ASGITransport``. It exists and it would exercise the FastAPI
adapter, but it is asynchronous only: ``httpx.Client`` cannot use it, and a
pytest consumer writes synchronous tests. The ASGI adapter is exercised by
:func:`vendorfake.testing.served` and :func:`vendorfake.testing.serve_in_thread`.
"""

from __future__ import annotations

import httpx

from vendorfake.core.kernel.unit import Unit, make_request
from vendorfake.core.transport.inprocess import TRANSPORT

__all__ = ["UnitTransport"]


def _headers(request: httpx.Request) -> dict[str, str]:
    """Names lower-cased, repeated names joined with ``", "`` -- the same
    shape ``vendorfake.asgi.adapt.request_headers`` produces over a socket."""
    headers: dict[str, str] = {}
    for raw_name, raw_value in request.headers.raw:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        existing = headers.get(name)
        headers[name] = value if existing is None else f"{existing}, {value}"
    return headers


class UnitTransport(httpx.BaseTransport):
    """``httpx.Client(transport=UnitTransport(unit))``."""

    __slots__ = ("_unit",)

    def __init__(self, unit: Unit) -> None:
        self._unit = unit

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # ``raw_path`` keeps the query string and percent-escapes intact;
        # ``make_request`` splits the query off and parses it the way every
        # other binding does, so a repeated key survives here too.
        raw_path = request.url.raw_path.decode("ascii")
        answered = self._unit.handle(
            make_request(
                method=request.method,
                path=raw_path,
                headers=_headers(request),
                raw_body=request.read(),
                transport=TRANSPORT,
            )
        )
        return httpx.Response(
            status_code=answered.status,
            headers=dict(answered.headers),
            content=answered.body,
            request=request,
        )
