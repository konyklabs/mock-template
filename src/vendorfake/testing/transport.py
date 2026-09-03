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

ONE DIFFERENCE FROM A SOCKET CLIENT: **an in-process call cannot time out.**
``handle_request`` never reads ``request.extensions["timeout"]`` -- it calls
``Unit.handle`` synchronously on this thread, so a consumer's ``timeout=`` is
silently not honoured and a call that blocks (a real-clock
``POST /__unit/webhooks/drain`` mid-cascade, say) blocks the test until it
finishes. The served fixtures honour timeouts; this one trades that for
never needing a socket.

A SECOND DIFFERENCE, DELIBERATE: **this binding fails a request no route
matched.** The kernel answers the vendor's own 404 with the diagnosis in
``Vendorfake-Near-Miss`` and never raises; here that becomes
:class:`UnmatchedRequest` unless the caller asks otherwise. In process, this
object is a test double, and a double that quietly answers 404 to a path
nobody serves lets a mis-targeted test pass against a unit it never reached --
which is the one failure mode every comparable tool (respx, pytest-httpx, MSW)
decided to make loud. A served unit stands in for the vendor, has a socket
rather than a stack to raise into, and answers as the vendor would.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from vendorfake.core.config.models import UnmatchedPolicy
from vendorfake.core.kernel.nearmiss import NEAR_MISS_HEADER
from vendorfake.core.kernel.unit import Unit, make_request
from vendorfake.core.transport.inprocess import TRANSPORT

__all__ = ["DEFAULT_INPROCESS_POLICY", "UnitTransport", "UnmatchedRequest"]

DEFAULT_INPROCESS_POLICY: UnmatchedPolicy = "error"
"""What an in-process binding does when the profile does not say.

Named rather than inlined because :func:`vendorfake.testing.unit` documents it
and a test asserts the two agree."""


class UnmatchedRequest(AssertionError):
    """A request reached the unit and no route in it matched.

    **An ``AssertionError``, not an ``httpx.HTTPError``.** The choice decides
    how pytest reports it: an ``AssertionError`` is a test *failure*, which is
    what "this test asked the fake for something it does not serve" is, while
    anything else is an *error* -- the category for a broken fixture or an
    environment problem. It also means a consumer who has wrapped their client
    calls in ``except httpx.HTTPError`` -- a retry loop under test, typically --
    does not swallow the diagnosis and turn it into a hang or a bare retry.

    The cost of the choice is that ``pytest.raises(httpx.HTTPError)`` will not
    catch it. That is the intended asymmetry: a consumer deliberately probing
    an unmodelled path should say so with ``unmatched="vendor-404"`` rather
    than by catching a transport error that never described what happened.
    """


def _unmatched_message(unit: Unit, method: str, path: str, header: str) -> str:
    """The whole diagnosis, in the shape a person reads in a pytest traceback.

    The near-miss table is parsed back out of the header rather than recomputed
    here: the kernel already did the work, and a binding that computed its own
    would be a second scorer to keep in step -- so a message that disagreed
    with ``GET /__unit/requests`` would be a bug nobody could see.
    """
    lines = [f"vendorfake: no route matched {method} {path} on {unit.name} (profile {unit.context.config.profile!r})"]
    try:
        misses: Any = json.loads(header)
    except ValueError:  # pragma: no cover - the kernel always writes valid JSON
        misses = []
    if misses:
        lines.append("Closest routes:")
        width = max(len(str(miss.get("route", ""))) for miss in misses)
        for miss in misses:
            operation = str(miss.get("operation_id") or "")
            lines.append(f"  {miss.get('route', '')!s:<{width}}  {operation:<24} {float(miss.get('score', 0)):.2f}")
    else:
        lines.append("This profile enables no route at all to compare against.")
    lines.append(
        "GET /__unit/routes lists every route this profile serves; "
        'pass unmatched="vendor-404" to unit() to receive the vendor\'s own 404 instead.'
    )
    return "\n".join(lines)


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
    """``httpx.Client(transport=UnitTransport(unit))``.

    ``unmatched`` decides what a request no route matched does here:
    ``"error"`` raises :class:`UnmatchedRequest`, ``"vendor-404"`` returns the
    unit's answer untouched. ``None``, the default, takes the profile's
    ``unmatched.policy`` and falls back to :data:`DEFAULT_INPROCESS_POLICY`.
    Precedence is the project's usual one, with the caller last and loudest:
    binding default < profile document / ``VENDORFAKE_UNMATCHED`` < this
    argument.
    """

    __slots__ = ("_unit", "_unmatched")

    def __init__(self, unit: Unit, *, unmatched: UnmatchedPolicy | None = None) -> None:
        self._unit = unit
        declared = unit.context.config.unmatched.policy
        self._unmatched: UnmatchedPolicy = (
            unmatched if unmatched is not None else (declared if declared is not None else DEFAULT_INPROCESS_POLICY)
        )

    @property
    def unmatched(self) -> UnmatchedPolicy:
        """The resolved policy, so a test can assert on it without inference."""
        return self._unmatched

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
        # The header is the signal, not the status: a vendor's own 404 for an
        # id that does not exist is a real answer from a real route and must
        # not fail the test. Only the kernel sets this header, and only where
        # nothing in the route table matched at all.
        near_miss = answered.headers.get(NEAR_MISS_HEADER)
        if near_miss is not None and self._unmatched == "error":
            raise UnmatchedRequest(_unmatched_message(self._unit, request.method, request.url.path, near_miss))
        return httpx.Response(
            status_code=answered.status,
            headers=dict(answered.headers),
            content=answered.body,
            request=request,
        )
