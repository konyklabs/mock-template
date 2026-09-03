"""An ``httpx`` transport that hands each request straight to a unit.

FOR: letting a consumer's test drive a unit with the client they already use
-- ``httpx.Client`` or ``httpx.AsyncClient`` -- without a socket, a port or a
server. The consumer writes ``client.post("/v2/orders", json=...)`` exactly as
they would against a served URL, and the request goes to :meth:`Unit.handle` in
this thread.

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

ONE CLASS, BOTH PROTOCOLS. :class:`UnitTransport` subclasses
``httpx.BaseTransport`` *and* ``httpx.AsyncBaseTransport``, which is the shape
``httpx.MockTransport`` uses, so one instance serves an ``httpx.Client`` and an
``httpx.AsyncClient`` over the same unit. That matters beyond convenience: an
async consumer (a FastAPI-style service injecting an ``AsyncClient``) otherwise
re-implements six lines of ASGI wiring per vendor and reaches into
``vendorfake.asgi``, which is internal. Two classes would have been two request
normalisations, and the parity above is exactly what must not be written twice.

``Unit.handle`` is synchronous and fast -- no I/O, one lock -- so
:meth:`handle_async_request` calls it inline rather than through a thread pool.
The event loop is held for a few hundred microseconds, and the alternative
would make id minting order depend on the pool's scheduling, which is the
determinism the request lock exists to protect. The one thing that *would* hold
the loop, a deliberate ``delay_ms``, is awaited rather than slept; see below.

TIMEOUTS: **honoured for a deliberate delay, and for nothing else.** Every
other call goes straight to the unit on this thread, so a consumer's
``timeout=`` cannot interrupt it and a call that blocks (a real-clock
``POST /__unit/webhooks/drain`` mid-cascade, say) blocks the test until it
finishes. What *is* honoured is the delay a ``timeout`` chaos fault asks for:
the unit answers immediately with :attr:`UnitResponse.delay_ms` set, and this
binding compares it against the client's read timeout. Longer, and the client
gets ``httpx.ReadTimeout`` **without anyone waiting** -- the point of the fault
is the consumer's retry path, and a test that has to spend five real seconds
proving a five-second timeout is a test nobody runs. Shorter, and the wait is
carried out for real, because a consumer asserting "my backoff waited" needs
elapsed time to move.

Earlier this module said an in-process call *cannot* time out, and that was
true while the kernel did the sleeping itself: nothing consulted
``request.extensions["timeout"]`` because nothing had anything to consult it
about. The served fixtures were the only way to rehearse a client timeout.
See ``vendorfake.core.chaos.faults`` for the other half of the change.

TRANSPORT-FIDELITY FAULTS, added alongside the timeout one. ``slow_body``
folds into the same read-timeout race as a deliberate delay, but on a
different number: whether the caller times out is decided against a single
chunk gap, not the sum of them, because a real read timeout is inactivity-
based per chunk and a served unit streaming many gaps each under it never
times a patient client out (see :func:`_would_exhaust_read_timeout_ms` and
``tests/integration/test_transport_faults_served.py``, which proves it against
a real socket). Once it is decided the caller waits, this binding -- which has
no real stream to hold open -- waits the aggregate once and hands back the
whole body, which is the honest simulation of "every chunk eventually
arrived". ``connection_reset`` and ``empty_response`` have no wait to race at
all: nothing here holds a socket to reset or starve, so this binding raises
the exception a real one would surface -- ``httpx.RemoteProtocolError`` and
``httpx.ReadError`` respectively -- immediately. See
:class:`~vendorfake.core.kernel.types.TransportDirective`.

A SECOND DIFFERENCE, DELIBERATE: **this binding fails a request no route
matched.** The kernel answers the vendor's own 404 with the diagnosis in
``Vendorfake-Near-Miss`` and never raises; here that becomes
:class:`UnmatchedRequest` unless the caller asks otherwise. In process, this
object is a test double, and a double that quietly answers 404 to a path
nobody serves lets a mis-targeted test pass against a unit it never reached --
which is the one failure mode every comparable tool (respx, pytest-httpx, MSW)
decided to make loud. A served unit stands in for the vendor, has a socket
rather than a stack to raise into, and answers as the vendor would.

WHY NOT ``httpx.ASGITransport``. It exists and it would exercise the FastAPI
adapter, but it is asynchronous only, so ``httpx.Client`` cannot use it and
half of this seam would be missing. The ASGI adapter is exercised by
:func:`vendorfake.testing.served` and :func:`vendorfake.testing.serve_in_thread`.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from typing import Any

import anyio
import httpx

from vendorfake.core.config.models import UnmatchedPolicy
from vendorfake.core.kernel.nearmiss import NEAR_MISS_HEADER
from vendorfake.core.kernel.types import UnitResponse
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
    top_operation = ""
    if misses:
        lines.append("Closest routes:")
        width = max(len(str(miss.get("route", ""))) for miss in misses)
        for miss in misses:
            operation = str(miss.get("operation_id") or "")
            if not top_operation:
                top_operation = operation
            lines.append(f"  {miss.get('route', '')!s:<{width}}  {operation:<24} {float(miss.get('score', 0)):.2f}")
    else:
        lines.append("This profile enables no route at all to compare against.")
    # The spec's own closing line (S-strict-and-request-log.md): point at
    # stream C's discovery helpers, now that they exist on this branch --
    # ``vendorfake.<vendor>.paths`` and ``Driver.path_for`` (roadmap#70) --
    # rather than at the lower-level ``GET /__unit/routes`` this used to name
    # in their place.
    example = f'path_for("{top_operation}")' if top_operation else "path_for(...)"
    lines.append(f'Use vendorfake.{unit.name}.paths or driver.{example}; or pass unmatched="vendor-404".')
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


def _read_timeout(request: httpx.Request) -> float | None:
    """The client's read timeout for this request, in seconds, or ``None``.

    ``httpx`` puts the resolved ``Timeout`` on ``request.extensions["timeout"]``
    as a plain mapping of the four phases, which is the documented way a
    transport learns what the caller asked for -- ``Client.timeout`` is not
    visible from here, and per-request ``timeout=`` would not be either.
    ``None`` means "no read timeout", which is a real setting and not a missing
    one, so it is returned as ``None`` rather than collapsed to zero.

    Defensive about the shape: a caller may put anything in ``extensions``, and
    a transport that raised on an unexpected entry would fail the request for a
    reason that has nothing to do with the request.
    """
    extensions: Mapping[str, Any] = request.extensions or {}
    timeout = extensions.get("timeout")
    if not isinstance(timeout, Mapping):
        return None
    read = timeout.get("read")
    if read is None:
        return None
    try:
        return float(read)
    except (TypeError, ValueError):  # pragma: no cover - a caller's malformed extension
        return None


def _wait_owed_ms(answered: UnitResponse) -> int:
    """How long this response should be held back, once it is decided the
    caller will wait for it at all (see :func:`_expired`, which decides that
    on a different number for ``slow_body``).

    Two faults ask a binding to wait: ``timeout`` (:attr:`UnitResponse.delay_ms`)
    and ``slow_body`` (:attr:`TransportDirective.chunk_bytes` /
    ``chunk_delay_ms``, read as the total of the gaps *between* chunks -- this
    binding has no real stream to hold open, so the honest way to simulate
    "every chunk eventually arrived" is to wait the aggregate once and hand
    back the whole body). The two cannot both be set: they come from different
    fault kinds, and fault selection arms at most one per request.
    """
    directive = answered.transport
    if directive is not None and directive.kind == "slow_body":
        return max(0, _slow_body_chunks(answered) - 1) * directive.chunk_delay_ms
    return answered.delay_ms


def _slow_body_chunks(answered: UnitResponse) -> int:
    """How many chunks a ``slow_body`` directive splits this body into -- the
    one number both the aggregate wait and the per-gap race derive from, so
    they cannot disagree about whether a gap exists at all."""
    directive = answered.transport
    if directive is None or directive.kind != "slow_body":
        return 1
    chunk_bytes = directive.chunk_bytes if directive.chunk_bytes > 0 else 64
    return max(1, math.ceil(len(answered.body) / chunk_bytes))


def _would_exhaust_read_timeout_ms(answered: UnitResponse) -> int:
    """The single gap a caller's read timeout actually races against.

    **Not** the aggregate :func:`_wait_owed_ms` computes. A real client's read
    timeout is inactivity-based per chunk -- httpx's own words are "the
    maximum duration to wait for a chunk of data to be received" -- so a
    served unit streaming ``slow_body`` genuinely never times a patient-enough
    client out no matter how many gaps there are, so long as no single one of
    them exceeds the read timeout; ``tests/integration/test_transport_faults_served.py``
    proves this against a real socket. Matching that here, rather than
    comparing the sum, is what keeps a test green in process green against a
    served unit too -- the parity this module's own docstring states as an
    invariant.
    """
    directive = answered.transport
    if directive is not None and directive.kind == "slow_body":
        # A body that fits in one chunk has no gap to race: a served unit
        # writes it in one go and a patient client never waits, so raising
        # here would fail in process a test that passes served -- the exact
        # parity break this function exists to prevent (found by review round
        # 2 of konyklabs/roadmap#73).
        if _slow_body_chunks(answered) <= 1:
            return 0
        return directive.chunk_delay_ms
    return answered.delay_ms


def _expired(request: httpx.Request, answered: UnitResponse) -> httpx.ReadTimeout | None:
    """The exception this wait should raise instead of being waited out.

    ``None`` when the wait fits inside what the caller is willing to wait, in
    which case the caller gets the response after a real wait. The comparison is
    strictly greater-than: a wait *equal* to the read timeout is the boundary
    case a socket resolves by racing, and answering is the choice that does not
    make a test flaky.
    """
    gap = _would_exhaust_read_timeout_ms(answered)
    if gap <= 0:
        return None
    read = _read_timeout(request)
    if read is None or gap / 1000.0 <= read:
        return None
    return httpx.ReadTimeout(
        f"the unit would hold this response back by at least {gap}ms, longer than the client's "
        f"{read}s read timeout (an injected fault; nothing waited)",
        request=request,
    )


def _rule_id(answered: UnitResponse) -> str:
    return answered.headers.get("vendorfake-rule", "?")


def _connection_fault(request: httpx.Request, answered: UnitResponse) -> Exception | None:
    """``connection_reset`` / ``empty_response``: no socket exists to reset or
    starve, so this binding raises the exception a real one would surface,
    directly and without waiting. See ``core/kernel/types.py``'s
    ``TransportDirective`` and the README's "Transport faults" section.
    """
    directive = answered.transport
    if directive is None:
        return None
    rule = _rule_id(answered)
    if directive.kind == "connection_reset":
        return httpx.RemoteProtocolError(f"vendorfake: connection reset by fault rule {rule}", request=request)
    if directive.kind == "empty_response":
        return httpx.ReadError(f"vendorfake: empty response by fault rule {rule}", request=request)
    return None


class UnitTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """``httpx.Client(transport=UnitTransport(unit))``, and the ``AsyncClient``
    of the same unit, off one instance.

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
        answered = self._answer(request, request.read())
        fault = _connection_fault(request, answered)
        if fault is not None:
            raise fault
        expired = _expired(request, answered)
        if expired is not None:
            raise expired
        owed = _wait_owed_ms(answered)
        if owed > 0:
            time.sleep(owed / 1000.0)
        return self._respond(request, answered)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """The same decision, on the caller's event loop.

        ``anyio.sleep`` rather than ``asyncio.sleep``: ``httpx`` depends on
        ``anyio``, so an ``AsyncClient`` is meant to be usable under any
        backend anyio supports, and an asyncio-only wait here would tie this
        transport to one of them regardless. Exercised on asyncio in this
        suite; nothing here is tested under trio.
        """
        answered = self._answer(request, await request.aread())
        fault = _connection_fault(request, answered)
        if fault is not None:
            raise fault
        expired = _expired(request, answered)
        if expired is not None:
            raise expired
        owed = _wait_owed_ms(answered)
        if owed > 0:
            await anyio.sleep(owed / 1000.0)
        return self._respond(request, answered)

    # -- shared by both protocols -------------------------------------------

    def _answer(self, request: httpx.Request, body: bytes) -> UnitResponse:
        """Hand the request to the unit. One normalisation, both protocols.

        The unit is called *before* the delay is judged, on either path, and
        that is the socket's order too: a caller whose read times out still had
        their request received and acted on. The ``timeout`` fault fires ahead
        of the handler, so nothing is written either way -- but a fault that
        did commit something would commit it here as well, which is the honest
        behaviour rather than a convenient one.

        ``body`` is passed in rather than read here because reading it is the
        one thing the two protocols do differently: ``aread()`` copes with a
        request whose content is an async iterator, and ``read()`` does not.
        """
        # ``raw_path`` keeps the query string and percent-escapes intact;
        # ``make_request`` splits the query off and parses it the way every
        # other binding does, so a repeated key survives here too.
        raw_path = request.url.raw_path.decode("ascii")
        answered = self._unit.handle(
            make_request(
                method=request.method,
                path=raw_path,
                headers=_headers(request),
                raw_body=body,
                transport=TRANSPORT,
            )
        )
        # The header is the signal, not the status: a vendor's own 404 for an
        # id that does not exist is a real answer from a real route and must
        # not fail the test. Only the kernel sets this header, and only where
        # nothing in the route table matched at all. Checked here, on the one
        # path both protocols share, so the sync and async clients agree.
        near_miss = answered.headers.get(NEAR_MISS_HEADER)
        if near_miss is not None and self._unmatched == "error":
            raise UnmatchedRequest(_unmatched_message(self._unit, request.method, request.url.path, near_miss))
        return answered

    @staticmethod
    def _respond(request: httpx.Request, answered: UnitResponse) -> httpx.Response:
        return httpx.Response(
            status_code=answered.status,
            headers=dict(answered.headers),
            content=answered.body,
            request=request,
        )
