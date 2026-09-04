"""A webhook endpoint on a real socket, recording exactly what arrived.

FOR: the half of a webhook test the fake cannot provide. A consumer's handler
verifies a signature over the bytes it received, so the fixture that stands in
for it must keep the bytes -- ``received`` holds ``(headers, raw_body)`` pairs
and never a parsed object, because a re-serialisation would verify a different
payload from the one delivered.

The response status is a function of the arrival index, so "reject the first
delivery, accept the retry" is one assignment, and the retry then really
crosses the wire.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__all__ = ["Delivery", "WebhookReceiver", "webhook_receiver"]


@dataclass(frozen=True, slots=True)
class Delivery:
    """One POST as it arrived: header names lower-cased, body untouched."""

    headers: dict[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


@dataclass
class WebhookReceiver:
    """Started by :func:`webhook_receiver`; ``url`` is what to subscribe.

    Only ``path`` is served; any other path answers 404 and records nothing.

    ``host`` is the loopback bind, so with an in-process or subprocess unit
    ``receiver.url`` is the whole story. A unit in a **container** cannot
    reach the host's loopback: bind ``host="0.0.0.0"`` and subscribe the
    address the container sees on ``port`` -- Docker Desktop and colima
    publish the host as ``http://host.docker.internal:{port}{path}``, and
    testcontainers as ``host.testcontainers.internal`` after
    ``exposeHostPorts`` (the Vitest example's ``setup/global.ts`` does
    exactly this). ``url`` refuses to guess a routable address for a
    wildcard bind.
    """

    path: str = "/webhooks"
    host: str = "127.0.0.1"
    #: Arrival index -> HTTP status to answer with. Defaults to 200 for all.
    respond_with: Callable[[int], int] = field(default=lambda index: 200)
    received: list[Delivery] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def port(self) -> int:
        assert self._server is not None, "the receiver is not started"
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        """The receiver as this machine reaches it. For a wildcard bind the
        routable name depends on who is asking (see the class docstring), so
        build that URL from :attr:`port` yourself."""
        host = "127.0.0.1" if self.host == "0.0.0.0" else self.host  # nosec B104  # a comparison, not a bind
        return f"http://{host}:{self.port}{self.path}"

    def start(self) -> None:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                if self.path != receiver.path:
                    # A delivery to the wrong path is recorded nowhere and
                    # answered 404, as a real receiver would: a consumer who
                    # mounted their handler on one path and subscribed another
                    # must see it fail, not a green test.
                    self.send_response(404)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                with receiver._lock:
                    index = len(receiver.received)
                    receiver.received.append(Delivery({k.lower(): v for k, v in self.headers.items()}, body))
                status = receiver.respond_with(index)
                self.send_response(status)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, fmt: str, *args: object) -> None:
                """Silence: the test's own output is the transcript."""

        self._server = ThreadingHTTPServer((self.host, 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def wait_for(self, count: int, *, timeout_s: float = 10.0) -> list[Delivery]:
        """Block until at least ``count`` deliveries arrived, or fail loudly.

        Deliveries are made from the unit's worker thread, so a test that
        asserts on ``received`` right after the request that caused them is
        racing that thread. ``drain()`` on the unit's driver is the other way
        to wait; this one is for the unit you cannot drain in-line -- one
        behind :func:`~vendorfake.testing.served`, or in a container (bound
        as the class docstring describes).
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.received) >= count:
                    return list(self.received)
            time.sleep(0.02)
        raise AssertionError(f"expected {count} webhook deliveries within {timeout_s}s, got {len(self.received)}")

    def clear(self) -> None:
        with self._lock:
            self.received.clear()


@contextmanager
def webhook_receiver(path: str = "/webhooks", *, host: str = "127.0.0.1") -> Iterator[WebhookReceiver]:
    """A receiver on a free port, stopped however the block ends.

    Loopback by default; ``host="0.0.0.0"`` only when a container must reach
    it (see :class:`WebhookReceiver` for the address to subscribe then).
    """
    receiver = WebhookReceiver(path=path, host=host)
    receiver.start()
    try:
        yield receiver
    finally:
        receiver.close()
