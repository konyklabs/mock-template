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

THREE WAYS TO REACH A UNIT, and the third is not a binding
----------------------------------------------------------
``inprocess`` and ``http`` are the two *bindings* the matrix runs, and both
build the unit in this interpreter -- ``http`` is uvicorn on a background
thread. ``subprocess`` is not a third binding but a third *process*: it is
declared in ``ConformanceTarget.out_of_process`` and never in ``transports``,
so nothing runs the whole matrix over it, and the one contract whose claim is
about separate runs opens it deliberately. Conflating the two is what let a
determinism contract compare two units that shared a pid and report
determinism.

THE TRIPWIRE IS WIRED HERE OR NOWHERE
-------------------------------------
``framework_answered`` needs the same :class:`FrameworkTripwire` handed to
``create_unit`` and to ``create_app``. This harness did neither, so the number
the unit reported was the literal 0 and the two contracts asserting on it could
not fail -- a verb dropped from ``HTTP_METHODS`` made Starlette answer a
request, the tripwire counted it, and the report printed "framework_answered
still 0". ``tests/conformance/test_harness_wiring.py`` drives that hole and
watches the number move.
"""

from __future__ import annotations

import functools
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from vendorfake.asgi import FrameworkTripwire, bind, bound_port, create_app
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


REPO_ROOT = Path(__file__).resolve().parents[2]

OUT_OF_PROCESS_TRANSPORT = "subprocess"
"""A unit built in a *separate OS process*, reached over HTTP.

Named apart from ``http`` and kept out of the matrix on purpose. The ``http``
transport below runs uvicorn on a background thread, which is a different
*binding* and the same interpreter -- exactly the confusion that let a
determinism contract compare two units that shared a pid, an import-time
counter and a hash seed, and call the result "deterministic across runs".
Spawning is slow enough that paying for it on every contract would be
indefensible, so it is paid for by the one contract whose claim requires it.
"""

CHILD_STARTUP_TIMEOUT_S = 60.0


def build_unit(profile: str, *, tripwire: FrameworkTripwire | None = None, vendor: str = VENDOR) -> Unit:
    """One unit for one check, built the same way for every transport.

    ``warn`` rather than the profile's own level: a matrix run builds close to
    a hundred units and each one logs an identical ``unit started`` line, which
    buries the report a reviewer is actually reading. Warnings and errors --
    a dead chaos rule, an undeclared capability -- still print, so this makes
    the run quieter and never less honest.

    The sink is the in-memory one because the suite builds two units to assert
    determinism, and a delivery sink that opened real connections to ``*.test``
    hostnames would make the webhook contracts a test of DNS.

    ``tripwire`` is threaded through to the control plane so that
    ``GET /__unit/health`` reports the *real* count of requests the web
    framework answered by itself. Passing ``None`` reports 0, and 0 is then the
    literal constant rather than a measurement -- which is precisely the state
    this harness was in: ``framework_answered`` was wired at neither end, so
    both contracts that assert on it asserted on a hardcoded zero and a real
    hole in the catch-all went unnoticed while the suite stayed green.
    """
    return create_unit(
        vendor=vendor,
        profile=profile,
        sink=MemorySink(),
        logger=JsonLogger("warn"),
        framework_answered=None if tripwire is None else tripwire.get,
    )


def _unit(profile: str, vendor: str = VENDOR) -> Unit:
    return build_unit(profile, vendor=vendor)


@contextmanager
def serving(unit: Unit, *, tripwire: FrameworkTripwire | None = None) -> Iterator[str]:
    """*This* unit on a real socket, yielding its base URL.

    Split out from :func:`serve` because ``--base-url`` needs the address and
    not a client: the conformance package never starts a server, so proving
    that entry point works means somebody else must start one and hand over a
    URL. That somebody is here.
    """
    app = create_app(unit, tripwire=tripwire)
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
def _served(profile: str, vendor: str = VENDOR) -> Iterator[ConformanceClient]:
    """The HTTP binding, with the tripwire wired at both ends.

    The unit is built with ``framework_answered=tripwire.get`` and the
    application is handed *the same object*. That is the whole wiring, and
    without it the number the unit reports is a constant: a verb missing from
    ``HTTP_METHODS`` made Starlette answer a request, the tripwire counted it,
    and the contract printed "framework_answered still 0" and passed.
    """
    tripwire = FrameworkTripwire()
    unit = build_unit(profile, tripwire=tripwire, vendor=vendor)
    try:
        with serving(unit, tripwire=tripwire) as base_url:
            client = HttpConformanceClient(base_url)
            try:
                yield client
            finally:
                client.close()
    finally:
        unit.stop()


@contextmanager
def _child(profile: str) -> Iterator[ConformanceClient]:
    """A unit built and served by a *separate process*, reached over HTTP.

    The child prints one JSON line naming its port and then serves; the parent
    never imports anything the child built. Nothing about this process -- its
    pid, its hash seed, its module globals -- is visible to it, which is the
    entire point: a scenario that hydrated differently per process is
    unobservable from two units in one interpreter.
    """
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.conformance.unit_child", "--profile", profile],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        yield from _client_onto(child)
    finally:
        _stop(child)


def _client_onto(child: subprocess.Popen[str]) -> Iterator[ConformanceClient]:
    """Read the child's handshake line and yield a client onto it."""
    assert child.stdout is not None
    deadline = time.monotonic() + CHILD_STARTUP_TIMEOUT_S
    line = child.stdout.readline()
    if not line:
        raise AssertionError(
            f"the out-of-process unit exited before announcing a port (rc={child.poll()}). "
            f"Run `python -m tests.conformance.unit_child --profile full` by hand to see why."
        )
    if time.monotonic() > deadline:  # pragma: no cover - a stuck child
        raise AssertionError(f"the out-of-process unit did not announce a port within {CHILD_STARTUP_TIMEOUT_S}s")
    announced = json.loads(line)
    client = HttpConformanceClient(f"http://{announced['host']}:{announced['port']}")
    try:
        yield client
    finally:
        client.close()


def _stop(child: subprocess.Popen[str]) -> None:
    child.terminate()
    try:
        child.wait(timeout=SHUTDOWN_TIMEOUT_S)
    except subprocess.TimeoutExpired:  # pragma: no cover - a wedged child
        child.kill()
        child.wait(timeout=SHUTDOWN_TIMEOUT_S)
    if child.stdout is not None:
        child.stdout.close()


@contextmanager
def _in_process(profile: str, vendor: str = VENDOR) -> Iterator[ConformanceClient]:
    unit = _unit(profile, vendor)
    try:
        yield InProcessConformanceClient(in_process(unit))
    finally:
        unit.stop()


@contextmanager
def open_client(profile: str, transport: str, vendor: str = VENDOR) -> Iterator[ConformanceClient]:
    if transport == "inprocess":
        with _in_process(profile, vendor) as client:
            yield client
    elif transport == "http":
        with _served(profile, vendor) as client:
            yield client
    elif transport == OUT_OF_PROCESS_TRANSPORT:
        with _child(profile) as client:
            yield client
    else:
        raise ValueError(
            f"unknown transport {transport!r}; this target offers 'inprocess', 'http' and {OUT_OF_PROCESS_TRANSPORT!r}"
        )


def target(
    *,
    profiles: tuple[str, ...] = PROFILES,
    transports: tuple[str, ...] = ("inprocess", "http"),
    out_of_process: tuple[str, ...] = (OUT_OF_PROCESS_TRANSPORT,),
) -> ConformanceTarget:
    return ConformanceTarget(
        name=VENDOR,
        open_client=open_client,
        profiles=profiles,
        transports=transports,
        out_of_process=out_of_process,
    )


CLOVER_VENDOR = "clover"
CLOVER_PROFILES: tuple[str, ...] = ("full",)
"""The one profile the clover unit ships at this commit; the six-profile set
arrives with PR E of konyklabs/roadmap#34, which is also when this target
can be run ``--strict`` (a single-profile matrix cannot satisfy the
anti-vacuity rule -- see :func:`one_profile_target`)."""


def clover_target(
    *,
    profiles: tuple[str, ...] = CLOVER_PROFILES,
    transports: tuple[str, ...] = ("inprocess", "http"),
) -> ConformanceTarget:
    """The second vendor, over the same in-process and HTTP bindings.

    No ``out_of_process`` transport yet: ``unit_child`` builds the Square unit
    by name, and the cross-process determinism contract has nothing to compare
    until the clover seed scenario exists. Recorded rather than faked.
    """
    return ConformanceTarget(
        name=CLOVER_VENDOR,
        open_client=functools.partial(open_client, vendor=CLOVER_VENDOR),
        profiles=profiles,
        transports=transports,
        out_of_process=(),
    )


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
