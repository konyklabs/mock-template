"""A webhook endpoint on a real socket, recording exactly what arrived.
``received`` holds ``(headers, raw_body)`` pairs and never a parsed object, a
signature being verified over the bytes delivered. The response status is a
function of the arrival index, so "reject the first, accept the retry" is one
assignment and the retry really crosses the wire."""

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
    """Started by :func:`webhook_receiver`; ``url`` is what to subscribe, and any
    other path answers 404 and records nothing. A unit in a container cannot reach
    the host's loopback: bind ``host="0.0.0.0"`` and subscribe the address the
    container sees on ``port``, ``url`` refusing to guess one for a wildcard."""

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
        """The receiver as this machine reaches it. Refuses to guess for a wildcard
        bind: the address a container reaches depends on who is asking."""
        if self.host == "0.0.0.0":  # nosec B104  # a comparison, not a bind
            raise ValueError(
                f"WebhookReceiver bound to 0.0.0.0 has no single URL: build it from .port ({self.port}) with the "
                f"address the subscriber reaches this host at, e.g. http://host.docker.internal:{self.port}{self.path}"
            )
        return f"http://{self.host}:{self.port}{self.path}"

    def start(self) -> None:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                if self.path != receiver.path:
                    # Recorded nowhere and answered 404, as a real receiver would.
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
        """Block until at least ``count`` deliveries arrived, else raise. Deliveries
        come from the unit's worker thread; ``drain()`` on a driver is the other
        way to wait, and this one is for a unit you cannot drain in-line."""
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
    """A receiver on a free port, stopped however the block ends. Loopback by
    default; ``host="0.0.0.0"`` only when a container must reach it, and see
    :class:`WebhookReceiver` for the address to subscribe then."""
    receiver = WebhookReceiver(path=path, host=host)
    receiver.start()
    try:
        yield receiver
    finally:
        receiver.close()
