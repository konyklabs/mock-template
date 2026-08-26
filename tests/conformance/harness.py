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
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.transport.inprocess import in_process
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.registry import create_unit

VENDOR = "square"

FAULTLESS_PROFILE = "no-faults"
"""The profile with no chaos capability -- named because two probes below aim
at it deliberately, and an index into ``PROFILES`` would not say why."""

PROFILES: tuple[str, ...] = (
    "full",
    "no-chaos",
    FAULTLESS_PROFILE,
    "orders-only",
    "oauth-only",
    "chaos-demo",
)

STARTUP_TIMEOUT_S = 30.0
SHUTDOWN_TIMEOUT_S = 10.0


def _unit(profile: str) -> Unit:
    """One unit for one check, built the same way for both transports.

    ``warn`` rather than the profile's own level: a matrix run builds close to
    a hundred units and each one logs an identical ``unit started`` line, which
    buries the report a reviewer is actually reading. Warnings and errors --
    a dead chaos rule, an undeclared capability -- still print, so this makes
    the run quieter and never less honest.

    The sink is the in-memory one because the suite builds two units to assert
    determinism, and a delivery sink that opened real connections to ``*.test``
    hostnames would make the webhook contracts a test of DNS.
    """
    return create_unit(vendor=VENDOR, profile=profile, sink=MemorySink(), logger=JsonLogger("warn"))


@contextmanager
def serving(unit: Unit) -> Iterator[str]:
    """*This* unit on a real socket, yielding its base URL.

    Split out from :func:`serve` because ``--base-url`` needs the address and
    not a client: the conformance package never starts a server, so proving
    that entry point works means somebody else must start one and hand over a
    URL. That somebody is here.
    """
    app = create_app(unit)
    sock: socket.socket = bind("127.0.0.1", 0)
    port = bound_port(sock)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while not server.started:
            if time.monotonic() > deadline:
                raise AssertionError(f"uvicorn did not start within {STARTUP_TIMEOUT_S}s")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(SHUTDOWN_TIMEOUT_S)
        sock.close()


@contextmanager
def serve(unit: Unit) -> Iterator[ConformanceClient]:
    """A client against *this* unit, over a real socket.

    Takes the unit rather than a profile name so that the mutant fixtures --
    which build their units through a different composition root in order to
    reach the control-plane and fault-selector seams -- serve the unit they
    mutated rather than a fresh unmutated one. A transport harness that quietly
    substituted a correct unit would make every out-of-process mutant result a
    lie, so there is one server function and it is handed its unit.
    """
    with serving(unit) as base_url:
        client = HttpConformanceClient(base_url)
        try:
            yield client
        finally:
            client.close()


@contextmanager
def _served(profile: str) -> Iterator[ConformanceClient]:
    unit = _unit(profile)
    try:
        with serve(unit) as client:
            yield client
    finally:
        unit.stop()


@contextmanager
def _in_process(profile: str) -> Iterator[ConformanceClient]:
    unit = _unit(profile)
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


def target(
    *,
    profiles: tuple[str, ...] = PROFILES,
    transports: tuple[str, ...] = ("inprocess", "http"),
) -> ConformanceTarget:
    return ConformanceTarget(name=VENDOR, open_client=open_client, profiles=profiles, transports=transports)


def one_profile_target() -> ConformanceTarget:
    """A target declaring a single profile that cannot meet every precondition.

    Named as a target so that ``--conformance-target`` can reach it from a
    subprocess. It exists to make the anti-vacuity rule falsifiable: on this
    target the whole matrix IS one profile, the two chaos contracts skip on it,
    and the pytest session must therefore go red even though every test it ran
    was green. Without this, "every contract passed somewhere" would be a claim
    no run has ever been seen to break.

    Both transports, deliberately: dropping one would make C10 skip as well and
    the probe would then be showing three failures for two different reasons.
    """
    return target(profiles=(FAULTLESS_PROFILE,))


def one_transport_target() -> ConformanceTarget:
    """The full profile matrix over a single binding.

    C10 compares two bindings, so it can only skip here -- and that skip is not
    in ``manifest.json``, which is what makes it the probe for
    ``--conformance-strict``.
    """
    return target(transports=("inprocess",))
