"""A ConformanceTarget for the vendor shipped in this distribution.

Deliberately on the test side of the tree and not inside the package. The
conformance suite must never import a web framework, and the ``http``
transport needs a real server; keeping the factory here is what lets the same
target offer both bindings without any module under ``src/vendorfake/`` doing
something the boundary checker would have to be widened for.

Every client is a freshly constructed unit with an in-memory sink: the suite
builds two units to assert determinism, and a delivery sink that opened real
connections to ``*.test`` hostnames would make the webhook contracts a test of
DNS.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn

from vendorfake.asgi import bind, bound_port, create_app
from vendorfake.conformance import ConformanceClient, ConformanceTarget, HttpConformanceClient
from vendorfake.conformance.client import InProcessConformanceClient
from vendorfake.core.transport.inprocess import in_process
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.registry import create_unit

VENDOR = "square"

PROFILES: tuple[str, ...] = (
    "full",
    "no-chaos",
    "no-faults",
    "orders-only",
    "oauth-only",
    "chaos-demo",
)

STARTUP_TIMEOUT_S = 30.0
SHUTDOWN_TIMEOUT_S = 10.0


@contextmanager
def _served(profile: str) -> Iterator[ConformanceClient]:
    unit = create_unit(vendor=VENDOR, profile=profile, sink=MemorySink())
    app = create_app(unit)
    sock: socket.socket = bind("127.0.0.1", 0)
    port = bound_port(sock)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    client = HttpConformanceClient(f"http://127.0.0.1:{port}")
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while not server.started:
            if time.monotonic() > deadline:
                raise AssertionError(f"uvicorn did not start within {STARTUP_TIMEOUT_S}s")
            time.sleep(0.01)
        yield client
    finally:
        client.close()
        server.should_exit = True
        thread.join(SHUTDOWN_TIMEOUT_S)
        sock.close()
        unit.stop()


@contextmanager
def _in_process(profile: str) -> Iterator[ConformanceClient]:
    unit = create_unit(vendor=VENDOR, profile=profile, sink=MemorySink())
    try:
        yield InProcessConformanceClient(in_process(unit))
    finally:
        unit.stop()


@contextmanager
def open_client(profile: str, transport: str) -> Iterator[ConformanceClient]:
    if transport == "inprocess":
        with _in_process(profile) as client:
            yield client
    elif transport == "http":
        with _served(profile) as client:
            yield client
    else:
        raise ValueError(f"unknown transport {transport!r}; this target offers 'inprocess' and 'http'")


def target(*, profiles: tuple[str, ...] = PROFILES) -> ConformanceTarget:
    return ConformanceTarget(
        name=VENDOR,
        open_client=open_client,
        profiles=profiles,
        transports=("inprocess", "http"),
    )
