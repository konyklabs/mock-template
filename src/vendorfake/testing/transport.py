"""An ``httpx`` transport that hands each request straight to a unit.

The bytes are the unit's bytes and the request is normalised the way
``vendorfake.asgi.adapt`` normalises it, so a test green here is green served.
One class subclasses both ``httpx.BaseTransport`` and ``httpx.AsyncBaseTransport``.
Timeouts are honoured for a deliberate delay and nothing else: a delay past the
client's read timeout raises ``httpx.ReadTimeout`` with nobody waiting, and on a
virtual clock the comparison uses ``Vendorfake-Delay-Ms``, so one rule means one
thing on both clocks. ``slow_body`` races a single chunk gap, not the sum. Unlike
a served unit, a request no route matched raises :class:`UnmatchedRequest` unless
the caller asks for ``"vendor-404"``.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from typing import Any

import anyio
import httpx

from vendorfake.core.config.models import UNMATCHED_POLICIES, UnmatchedPolicy
from vendorfake.core.kernel.nearmiss import NEAR_MISS_HEADER
from vendorfake.core.kernel.types import UnitResponse
from vendorfake.core.kernel.unit import DELAY_ASKED_HEADER, Unit, make_request
from vendorfake.core.transport.inprocess import TRANSPORT

__all__ = ["DEFAULT_INPROCESS_POLICY", "UnitTransport", "UnmatchedRequest", "checked_unmatched"]

DEFAULT_INPROCESS_POLICY: UnmatchedPolicy = "error"
"""What an in-process binding does when the profile does not say."""


def checked_unmatched(value: object) -> UnmatchedPolicy | None:
    """``value`` as an :data:`UnmatchedPolicy`, or a ``ValueError`` naming both."""
    if value is None:
        return None
    for policy in UNMATCHED_POLICIES:
        if value == policy:
            return policy
    raise ValueError(
        f"unmatched={value!r} is not one of {', '.join(repr(p) for p in UNMATCHED_POLICIES)}. "
        f'"error" raises UnmatchedRequest for a request no route matched; "vendor-404" answers '
        f"the vendor's own 404 with the diagnosis in the Vendorfake-Near-Miss header."
    )


class UnmatchedRequest(AssertionError):
    """A request reached the unit and no route matched. An ``AssertionError``, so
    pytest reports a failure and ``except httpx.HTTPError`` cannot swallow it."""


def _unmatched_message(unit: Unit, method: str, path: str, header: str) -> str:
    """The diagnosis as a pytest traceback shows it, parsed back out of the header
    rather than recomputed, so no second scorer can drift."""
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
    example = f'path_for("{top_operation}")' if top_operation else "path_for(...)"
    lines.append(f'Use vendorfake.{unit.name}.paths or driver.{example}; or pass unmatched="vendor-404".')
    return "\n".join(lines)


def _headers(request: httpx.Request) -> dict[str, str]:
    """Names lower-cased, repeated names joined with ``", "``, as over a socket."""
    headers: dict[str, str] = {}
    for raw_name, raw_value in request.headers.raw:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        existing = headers.get(name)
        headers[name] = value if existing is None else f"{existing}, {value}"
    return headers


def _read_timeout(request: httpx.Request) -> float | None:
    """The client's read timeout in seconds, off ``request.extensions["timeout"]``;
    ``None`` for "no read timeout" or for any unexpected shape, never raising."""
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
    """How long to hold this response back once :func:`_expired` decided the caller
    waits: ``delay_ms`` for ``timeout``, the total of the gaps for ``slow_body``,
    at most one of which arms per request."""
    directive = answered.transport
    if directive is not None and directive.kind == "slow_body":
        return max(0, _slow_body_chunks(answered) - 1) * directive.chunk_delay_ms
    return answered.delay_ms


def _slow_body_chunks(answered: UnitResponse) -> int:
    """How many chunks ``slow_body`` splits this body into; both the aggregate
    wait and the per-gap race derive from it, so they cannot disagree."""
    directive = answered.transport
    if directive is None or directive.kind != "slow_body":
        return 1
    chunk_bytes = directive.chunk_bytes if directive.chunk_bytes > 0 else 64
    return max(1, math.ceil(len(answered.body) / chunk_bytes))


def _would_exhaust_read_timeout_ms(answered: UnitResponse) -> int:
    """The single gap a read timeout races against, not the aggregate: a timeout
    is inactivity-based per chunk."""
    directive = answered.transport
    if directive is not None and directive.kind == "slow_body":
        # A body that fits in one chunk has no gap to race, so nothing expires.
        if _slow_body_chunks(answered) <= 1:
            return 0
        return directive.chunk_delay_ms
    return answered.delay_ms


def _expired(request: httpx.Request, answered: UnitResponse) -> httpx.ReadTimeout | None:
    """The exception this wait raises instead of being waited out, or ``None`` when
    it fits the read timeout; strictly greater-than, so an equal wait answers."""
    gap = max(_would_exhaust_read_timeout_ms(answered), _delay_asked_ms(answered))
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


def _delay_asked_ms(answered: UnitResponse) -> int:
    """The delay a ``timeout`` rule asked for, off :data:`DELAY_ASKED_HEADER`: the
    only number that survives a virtual clock. Zero when the header is absent."""
    raw = answered.headers.get(DELAY_ASKED_HEADER)
    if raw is None:
        return 0
    try:
        return max(0, math.ceil(float(raw)))
    except ValueError:  # pragma: no cover - the kernel writes this header from a number
        return 0


def _rule_id(answered: UnitResponse) -> str:
    return answered.headers.get("vendorfake-rule", "?")


def _connection_fault(request: httpx.Request, answered: UnitResponse) -> Exception | None:
    """``connection_reset`` / ``empty_response``: with no socket to reset, raise
    the exception a real one would surface, without waiting."""
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
    """``httpx.Client(transport=UnitTransport(unit))``, and the ``AsyncClient`` of
    the same unit, off one instance. ``unmatched`` decides what a request no route
    matched does: ``"error"`` raises, ``"vendor-404"`` returns the unit's answer,
    and ``None`` takes the profile's policy then the binding default."""

    __slots__ = ("_unit", "_unmatched")

    def __init__(self, unit: Unit, *, unmatched: UnmatchedPolicy | None = None) -> None:
        self._unit = unit
        self._unmatched: UnmatchedPolicy = checked_unmatched(unmatched) or DEFAULT_INPROCESS_POLICY

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
        """The same decision on the caller's loop; ``anyio.sleep`` ties no backend."""
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
        """Hand the request to the unit; one normalisation, both protocols. It is
        called before the delay is judged, as over a socket, and ``body`` is passed
        in because reading it is what the two protocols do differently."""
        # ``raw_path`` keeps the query string and percent-escapes intact.
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
        # The header is the signal, not the status: a vendor's own 404 for a
        # missing id is a real answer from a real route and must not fail.
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
