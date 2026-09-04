"""Outbound transport for one delivery attempt.

FOR: keeping "how a delivery leaves the process" out of the dispatcher. The
dispatcher decides *what* to send, to whom, when to retry and what to record;
:class:`DeliverySink` decides how the bytes leave.

INVARIANT: **the sink is the only place in the core that may reach the network,
and it is the only place ``httpx`` may be imported.** ``tools/boundary.toml``
records that permission. The invariant it protects is the one D-001 exists for:
the core does not assume HTTP. A dispatcher that called ``httpx`` itself would
make that claim untestable, because there would be no seam at which to prove
it.

WHY ``SinkResult.status == 0`` IS A CONTRACT AND NOT AN ACCIDENT. There is no
status when the transport failed before a response existed, and the reference
uses ``0`` for that (``sink.ts:45``, ``status: 0``), which
``dispatcher.ts:310`` then reads back to classify the failure. Ported
literally, including ``MemorySink``'s callable form returning ``0`` -- "index 0
returns 0, i.e. the subscriber timed out" is how the timeout test is written at
all, and a sink that raised instead would have no way to say it.

WHY ``MemorySink.respond_with`` KEEPS ITS CALLABLE FORM. ``(req, call_index) ->
int`` is what makes "fail the first two attempts, then accept" a one-line test
setup. Replacing it with a list of statuses would be tidier and would silently
change what happens when the dispatcher makes more attempts than the list has
entries, which is exactly the case the retry-exhaustion test is about.

THE SEND IS SYNCHRONOUS, like everything else in the core. It runs on the
delivery worker's thread, never on a request thread, so a subscriber that takes
the full ``timeout_ms`` to answer costs one background thread and no request.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

__all__ = [
    "DeliverySink",
    "HttpSink",
    "MemorySink",
    "SinkRequest",
    "SinkResult",
]

_SNIPPET_LIMIT = 200
"""How much of a subscriber's response body is kept. The reference's
``text.slice(0, 200)``; enough to recognise an error page, short enough that a
misbehaving subscriber cannot fill the delivery log with its own HTML."""


@dataclass(frozen=True, slots=True)
class SinkRequest:
    """One outbound delivery, fully formed.

    ``body`` is bytes and not an object: the signature was computed over these
    exact bytes before this request existed, and a sink that re-serialised
    would invalidate it while every assertion still passed.
    """

    url: str
    headers: Mapping[str, str]
    body: bytes
    timeout_ms: int


@dataclass(frozen=True, slots=True)
class SinkResult:
    """What came back. ``status`` is ``0`` when nothing did."""

    status: int
    body_snippet: str | None = None
    error: str | None = None
    #: True when nothing came back in time. Distinct from ``status == 0``,
    #: which also covers a connection that was refused outright.
    timed_out: bool = False


class DeliverySink(Protocol):
    """Where a delivery goes. Two members, and neither of them mentions HTTP."""

    @property
    def kind(self) -> str:
        """Short name reported at ``/__unit/info``: ``http``, ``memory``, ``file``."""
        ...

    def send(self, req: SinkRequest) -> SinkResult:
        """Deliver once. Never raises: a failure is a :class:`SinkResult`.

        The dispatcher's retry decision is driven entirely by the returned
        value, so a sink that raised would turn a retryable failure into an
        unhandled exception on the delivery worker.
        """
        ...


class MemorySink:
    """Captures deliveries in memory. The conformance suite's sink, and tests'.

    ``received`` holds a copy of every request in the order the dispatcher made
    it, which is the observation most delivery tests are actually about: with
    one delivery worker that order is determined rather than merely likely.
    """

    kind = "memory"

    def __init__(self, respond_with: int | Callable[[SinkRequest, int], int] = 200) -> None:
        self.received: list[SinkRequest] = []
        #: A status, or a function of ``(request, call_index)`` returning one.
        #: ``call_index`` is 0-based and counts calls to *this* sink, not
        #: attempts for one event.
        self.respond_with: int | Callable[[SinkRequest, int], int] = respond_with
        self._lock = threading.Lock()

    def send(self, req: SinkRequest) -> SinkResult:
        with self._lock:
            index = len(self.received)
            self.received.append(req)
        responder = self.respond_with
        status = responder(req, index) if callable(responder) else responder
        if status == 0:
            return SinkResult(status=0, error="simulated transport failure", timed_out=True)
        return SinkResult(status=status)

    def clear(self) -> None:
        """Forget every delivery. Useful between phases of one long test."""
        with self._lock:
            self.received.clear()


class HttpSink:
    """Posts each delivery over HTTP. The default sink for a running unit.

    The client is built on first use rather than at construction, so a unit
    whose vendor has no webhooks -- or a test that never delivers -- opens no
    connection pool and leaks no file descriptor. Requests are made from one
    delivery worker thread, and ``httpx.Client`` is documented thread-safe, so
    one client is shared rather than one per attempt.
    """

    kind = "http"

    def __init__(self, *, verify: bool = True) -> None:
        self._verify = verify
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _ensure_client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(verify=self._verify, follow_redirects=False)
            return self._client

    def send(self, req: SinkRequest) -> SinkResult:
        """Post once. Every failure mode becomes a :class:`SinkResult`.

        Three outcomes, matching the three :class:`DeliveryOutcome` members the
        dispatcher classifies into: a status came back, nothing came back in
        time, or the transport failed. ``follow_redirects`` is off because a
        subscriber that answers ``302`` has not accepted the delivery, and
        following the redirect would report the redirect target's status as if
        it were the subscriber's.
        """
        client = self._ensure_client()
        try:
            res = client.post(
                req.url,
                headers=dict(req.headers),
                content=req.body,
                timeout=req.timeout_ms / 1000.0,
            )
        except httpx.TimeoutException as exc:
            return SinkResult(status=0, error=_describe(exc), timed_out=True)
        except httpx.HTTPError as exc:
            return SinkResult(status=0, error=_describe(exc), timed_out=False)
        return SinkResult(status=res.status_code, body_snippet=res.text[:_SNIPPET_LIMIT])

    def close(self) -> None:
        """Release the connection pool. Called from ``Unit.stop``."""
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()


def _describe(exc: BaseException) -> str:
    """A one-line description that never collapses to the empty string.

    ``str(httpx.ConnectError())`` is often ``''``, and an empty ``error`` on a
    delivery record is indistinguishable from no error at all -- which is the
    difference between "the subscriber refused the connection" and "the
    subscriber accepted it".
    """
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
