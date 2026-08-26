"""Running the ASGI application on a real socket.

FOR: the one thing between ``create_app`` and a listening port -- binding,
reporting which port was actually bound, and shutting down cleanly.

INVARIANT: **the port is known before the server starts.** ``port=0`` means
"any free port", which is how a test starts a server without racing another
test for a fixed number -- but a server that binds its own socket only tells
you the number once it is already serving, and the caller that needs to print
it is blocked inside ``run()`` by then. So the socket is bound here first, its
number read off it, and the bound socket handed to uvicorn. A caller therefore
gets the port synchronously, before a single request could arrive.

INVARIANT: **this module builds no unit.** It takes an application and runs it.
Constructing a unit means resolving a vendor and loading a profile, which lives
in ``vendorfake.registry`` -- a module the boundary policy forbids this package
from importing, and rightly: the transport adapter must stay a thing you can
put in front of any unit, not a second place that knows how units are made.
The CLI owns that wiring and passes the finished application in.

Signals are left to uvicorn, which installs handlers for ``SIGINT`` and
``SIGTERM`` and runs its own graceful-shutdown path: stop accepting, let
in-flight requests finish, then close. Re-implementing that here would only
give it a second, worse version.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "bind", "run_server"]

DEFAULT_HOST = "127.0.0.1"
"""Loopback, not ``0.0.0.0``.

A fake holds seeded credentials and answers anything that asks; the default
should not be reachable from the network. A container image overrides it
explicitly, which is the one place where publishing on all interfaces is the
intent rather than an oversight."""

DEFAULT_PORT = 8080
"""Matches the profile loader's ``transport.port`` default, so the flag, the
environment variable and the profile cannot disagree about what "no port given"
means."""

_BACKLOG = 128


def bind(host: str, port: int) -> socket.socket:
    """Bind a listening socket and return it, already listening.

    ``SO_REUSEADDR`` so a restart is not blocked by a socket in ``TIME_WAIT``,
    which for a fake that a test suite starts and stops repeatedly is the
    difference between working and failing every other run.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(_BACKLOG)
    return sock


def bound_port(sock: socket.socket) -> int:
    """The port a socket actually got, which for ``port=0`` is the only way to
    learn it."""
    return int(sock.getsockname()[1])


def run_server(
    app: FastAPI,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    log_level: str = "info",
    on_bound: Callable[[str, int], None] | None = None,
) -> None:
    """Serve ``app`` until interrupted. Blocking.

    ``on_bound`` is called once, with the host and the real port, after the
    socket is listening and before uvicorn takes it over. That ordering is what
    makes ``--port 0`` usable from a parent process: the number is printed and
    flushed while the parent is still reading, rather than after the server
    has started answering requests the parent could not yet address.
    """
    sock = bind(host, port)
    if on_bound is not None:
        on_bound(host, bound_port(sock))
    config = uvicorn.Config(app, log_level=log_level, access_log=False)
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
