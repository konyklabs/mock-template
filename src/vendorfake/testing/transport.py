"""An ``httpx`` transport that hands each request straight to a unit.

FOR: letting a consumer's test drive a unit with the client they already use
-- ``httpx.Client`` -- without a socket, a port or an event loop. The consumer
writes ``client.post("/v2/orders", json=...)`` exactly as they would against a
served URL, and the request goes to :meth:`Unit.handle` in this thread.

INVARIANT: **the bytes are the unit's bytes.** The request body is read once
and handed over untouched as ``raw_body``; the response body is the unit's
``UnitResponse.body`` placed into an ``httpx.Response`` without re-encoding.
Conformance contract C10 proves the in-process and HTTP bindings agree byte
for byte, so a test that passes here passes against the served unit and the
container -- that is the property that makes an in-process fixture a fair
rehearsal rather than a convenience.

WHY NOT ``httpx.ASGITransport``. It exists and it would exercise the FastAPI
adapter, but it is asynchronous only: ``httpx.Client`` cannot use it, and a
pytest consumer writes synchronous tests. The ASGI adapter is exercised by
:func:`vendorfake.testing.served`, which runs the real server.
"""

from __future__ import annotations

import httpx

from vendorfake.core.kernel.unit import Unit, make_request
from vendorfake.core.transport.inprocess import TRANSPORT

__all__ = ["UnitTransport"]


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
        headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in request.headers.raw}
        answered = self._unit.handle(
            make_request(
                method=request.method,
                path=raw_path,
                headers=headers,
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
