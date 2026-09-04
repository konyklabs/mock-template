"""Running the ASGI application on a real socket. Bound here and handed to
uvicorn, so a caller using ``port=0`` learns the number synchronously; this
module builds no unit, because the boundary policy forbids the registry import.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

try:
    import uvicorn
    from fastapi import FastAPI
except ImportError as exc:
    raise ImportError("vendorfake serve needs the 'serve' extra: pip install 'vendorfake[serve]'") from exc

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "bind", "run_server", "serve_in_thread"]

_THREAD_STARTUP_TIMEOUT_S = 30.0
_THREAD_SHUTDOWN_TIMEOUT_S = 10.0

DEFAULT_HOST = "127.0.0.1"
"""Loopback: a fake holds seeded credentials, so widening it is explicit."""

DEFAULT_PORT = 8080
"""Matches the profile loader's ``transport.port`` default."""

_BACKLOG = 128


def bind(host: str, port: int) -> socket.socket:
    """Bind and listen; ``SO_REUSEADDR`` so ``TIME_WAIT`` cannot block a restart."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(_BACKLOG)
    return sock


def bound_port(sock: socket.socket) -> int:
    """The port a socket actually got, the only way to learn it for ``port=0``."""
    return int(sock.getsockname()[1])


def run_server(
    app: FastAPI,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    log_level: str = "info",
    on_bound: Callable[[str, int], None] | None = None,
) -> None:
    """Serve ``app`` until interrupted. Blocking. ``on_bound`` is called once with
    the host and real port before uvicorn takes over, so ``--port 0`` is readable
    by a parent process."""
    sock = bind(host, port)
    if on_bound is not None:
        on_bound(host, bound_port(sock))
    config = uvicorn.Config(app, log_level=log_level, access_log=False)
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


@contextmanager
def serve_in_thread(
    app: FastAPI,
    *,
    host: str = DEFAULT_HOST,
    port: int = 0,
    log_level: str = "error",
) -> Iterator[str]:
    """Serve ``app`` on a background thread, yielding its base URL. A thread, not
    a process; separate runs need ``vendorfake.testing.served``."""
    sock = bind(host, port)
    number = bound_port(sock)
    server = uvicorn.Server(uvicorn.Config(app, log_level=log_level, access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + _THREAD_STARTUP_TIMEOUT_S
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("uvicorn exited before it started serving")
            if time.monotonic() > deadline:
                raise RuntimeError(f"uvicorn did not start within {_THREAD_STARTUP_TIMEOUT_S}s")
            time.sleep(0.01)
        yield f"http://{host}:{number}"
    finally:
        server.should_exit = True
        thread.join(_THREAD_SHUTDOWN_TIMEOUT_S)
        sock.close()
