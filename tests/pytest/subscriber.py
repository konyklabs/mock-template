"""A webhook subscriber that behaves like a consumer's own endpoint.

Records the RAW request bytes (the signature covers them, so a re-serialized
body would not verify) and can reject deliveries so the unit's retry behaviour
is exercised across a real socket.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


@dataclass
class Received:
    headers: dict[str, str]
    raw_body: bytes


@dataclass
class Subscriber:
    port: int
    received: list[Received] = field(default_factory=list)
    respond_with: int | Callable[[int], int] = 200
    _server: ThreadingHTTPServer | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def status_for(self, index: int) -> int:
        return self.respond_with(index) if callable(self.respond_with) else self.respond_with

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def start_subscriber() -> Subscriber:
    state = Subscriber(port=0)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length)
            index = len(state.received)
            state.received.append(Received(headers={k.lower(): v for k, v in self.headers.items()}, raw_body=raw))
            status = state.status_for(index)
            payload = b"ok" if 200 <= status < 300 else b"nope"
            self.send_response(status)
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            """Silence the default stderr access log."""

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    state.port = server.server_address[1]
    state._server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state._thread = thread
    return state
