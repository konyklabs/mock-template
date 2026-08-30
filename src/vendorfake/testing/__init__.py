"""``vendorfake.testing`` -- the fixtures a consumer's test suite reaches for.

FOR: getting an integration team from ``pip install vendorfake`` to a green
test in minutes, without learning how units, profiles, bindings or the control
plane fit together. Three ways to hold a unit, one shape once you have it:

:func:`unit`
    A unit in this process, driven through an ``httpx.Client`` with no socket.
    Fast enough to build per test. Webhooks still go out over real HTTP to
    whatever URL is subscribed, so a :func:`webhook_receiver` on loopback sees
    signed deliveries exactly as a served unit would send them.

:func:`served`
    The shipped command, ``vendorfake serve``, in a child process, with a real
    URL. For a service under test that needs a base URL in its configuration.

:func:`serve_in_thread`
    A real server on a background thread in this interpreter, from a unit
    built by :func:`unit`. A URL without a second process.

Whichever way, the object yielded is a :class:`Driver`: ``client`` speaks to
the vendor surface, ``seed`` names what the scenario already contains, and the
methods wrap the control plane -- subscribe, drain, reset, add a chaos rule --
so a consumer's test says what it means rather than which ``/__unit`` route
does it.

Everything here builds on the same public pieces the README's ``curl``
commands use, so nothing observable differs between a test written against
these helpers and one written against the container.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from vendorfake.core.kernel.types import Logger
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.webhooks.sink import DeliverySink
from vendorfake.registry import create_unit
from vendorfake.testing.receiver import Delivery, WebhookReceiver, webhook_receiver
from vendorfake.testing.seeds import CloverSeed, SquareSeed, seed_for
from vendorfake.testing.transport import UnitTransport

__all__ = [
    "CloverSeed",
    "Delivery",
    "Driver",
    "ServedUnit",
    "SquareSeed",
    "StartedUnit",
    "UnitTransport",
    "WebhookReceiver",
    "serve_in_thread",
    "served",
    "unit",
    "webhook_receiver",
]

IN_PROCESS_BASE_URL = "http://vendorfake.local"
"""The host an in-process client addresses. Never resolved: the transport
hands every request to the unit, and the name only exists so relative paths
in ``client.get("/v2/locations")`` have something to be relative to."""

STARTUP_TIMEOUT_S = 60.0
SHUTDOWN_TIMEOUT_S = 15.0
_LISTENING = re.compile(r"listening on http://([^:\s]+):(\d+)")


@dataclass
class Driver:
    """A unit you can talk to, however it was started."""

    vendor: str
    profile: str
    base_url: str
    client: httpx.Client
    seed: SquareSeed | CloverSeed | None

    # -- reading ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/health"))

    def info(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/info"))

    def deliveries(self) -> list[dict[str, Any]]:
        """Every webhook delivery attempt the unit made, oldest first."""
        return list(self._json(self.client.get("/__unit/webhooks/deliveries"))["deliveries"])

    # -- webhooks ------------------------------------------------------------

    def subscribe(
        self,
        notification_url: str,
        event_types: Sequence[str],
        signature_key: str,
        *,
        id: str | None = None,
    ) -> dict[str, Any]:
        """Register a subscriber through the control plane, pre-verified.

        Vendor-neutral on purpose: Square has a subscriptions API and Clover
        has a dashboard, and a test that only wants deliveries to arrive
        should not have to know which. ``signature_key`` is the HMAC key for
        Square and the ``X-Clover-Auth`` code for Clover.
        """
        body: dict[str, Any] = {
            "notification_url": notification_url,
            "event_types": list(event_types),
            "signature_key": signature_key,
        }
        if id is not None:
            body["id"] = id
        return self._json(self.client.post("/__unit/webhooks/subscriptions", json=body), expect=(200, 201))

    def drain(self) -> None:
        """Block until every pending delivery -- retries included -- has settled."""
        self._json(self.client.post("/__unit/webhooks/drain", json={}))

    # -- state and faults ----------------------------------------------------

    def reset(self) -> dict[str, Any]:
        """Back to the seed scenario. Subscribers registered through the
        control plane survive; everything a test created does not."""
        return self._json(self.client.post("/__unit/state/reset", json={}))

    def add_chaos_rule(self, rule: Mapping[str, Any]) -> dict[str, Any]:
        """Arm one deterministic fault. See ``GET /__unit/chaos`` for the
        catalogue and the README for the rule shape."""
        return self._json(self.client.post("/__unit/chaos/rules", json=dict(rule)))

    def reset_chaos(self) -> None:
        """Disarm every rule a test added, and every rule's counters."""
        self._json(self.client.post("/__unit/chaos/reset", json={}))

    def advance_clock(self, ms: int) -> dict[str, Any]:
        """Move a virtual clock forward. Refused by the unit on a real one."""
        return self._json(self.client.post("/__unit/clock/advance", json={"ms": ms}))

    @staticmethod
    def _json(response: httpx.Response, *, expect: tuple[int, ...] = (200,)) -> dict[str, Any]:
        if response.status_code not in expect:
            raise RuntimeError(
                f"{response.request.method} {response.request.url.path} answered {response.status_code}: "
                f"{response.text[:400]}"
            )
        document: dict[str, Any] = response.json()
        return document


@dataclass
class StartedUnit(Driver):
    """From :func:`unit`: the unit itself is reachable for anything the
    control plane does not cover."""

    unit: Unit = field(kw_only=True)


@dataclass
class ServedUnit(Driver):
    """From :func:`served`: a child process the block will stop."""

    process: subprocess.Popen[str] = field(kw_only=True)

    @property
    def pid(self) -> int:
        return self.process.pid


def _seed_of(built: Unit) -> SquareSeed | CloverSeed | None:
    return seed_for(built.name, built.context.config.vendor_config)


@contextmanager
def unit(
    vendor: str,
    profile: str = "full",
    *,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
) -> Iterator[StartedUnit]:
    """A unit in this process, stopped however the block ends.

    ``sink`` defaults to real HTTP delivery, so a subscribed
    :func:`webhook_receiver` sees signed bytes; pass
    ``vendorfake.core.webhooks.sink.MemorySink()`` to capture in memory
    instead. ``env`` is the ``VENDORFAKE_*`` layer, empty by default -- the
    process environment is never read here, so one test's variables cannot
    change another test's profile.
    """
    built = create_unit(
        vendor=vendor,
        profile=profile,
        env=env,
        sink=sink,
        logger=JsonLogger("warn") if logger is None else logger,
    )
    try:
        with httpx.Client(transport=UnitTransport(built), base_url=IN_PROCESS_BASE_URL) as client:
            yield StartedUnit(
                vendor=built.name,
                profile=built.context.config.profile,
                base_url=IN_PROCESS_BASE_URL,
                client=client,
                seed=_seed_of(built),
                unit=built,
            )
    finally:
        built.stop()


@contextmanager
def serve_in_thread(started: StartedUnit, *, host: str = "127.0.0.1", port: int = 0) -> Iterator[Driver]:
    """A real server in front of ``started``'s unit, on a background thread.

    Yields a second :class:`Driver` onto the *same* unit, so state written
    through either client is visible through the other. The import is local
    because this is the only function here that needs the web framework, and
    :func:`unit` alone should not pay for it.
    """
    from vendorfake.asgi import create_app
    from vendorfake.asgi import serve_in_thread as serve_app

    with (
        serve_app(create_app(started.unit), host=host, port=port) as base_url,
        httpx.Client(base_url=base_url) as client,
    ):
        yield Driver(
            vendor=started.vendor,
            profile=started.profile,
            base_url=base_url,
            client=client,
            seed=started.seed,
        )


@contextmanager
def served(
    vendor: str,
    profile: str = "full",
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    log_level: str = "error",
    timeout_s: float = STARTUP_TIMEOUT_S,
) -> Iterator[ServedUnit]:
    """``vendorfake serve`` in a child process, with its URL.

    Runs the interpreter this test runs under (``python -m vendorfake``), so
    whatever environment installed ``vendorfake`` serves it. ``port=0`` lets
    the operating system choose, and the CLI announces the number before it
    accepts a request. The child is asked to stop with ``SIGTERM`` -- uvicorn's
    graceful path -- and killed only if it ignores that.
    """
    argv = [
        sys.executable,
        "-m",
        "vendorfake",
        "serve",
        "--vendor",
        vendor,
        "--profile",
        profile,
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        log_level,
    ]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        base_url = _wait_for_announcement(process, timeout_s)
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            health = client.get("/__unit/health").json()
            yield ServedUnit(
                vendor=str(health["vendor"]),
                profile=str(health["profile"]),
                base_url=base_url,
                client=client,
                # The child's profile is not readable over the wire, so the
                # application credentials are the vendor's defaults -- what
                # every shipped profile sets. A custom profile that overrides
                # them is a case for `unit()`, where the seed reads the config.
                seed=seed_for(str(health["vendor"]), {}),
                process=process,
            )
    finally:
        _stop(process)


def _wait_for_announcement(process: subprocess.Popen[str], timeout_s: float) -> str:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line:
            found = _LISTENING.search(line)
            if found is not None:
                return f"http://{found.group(1)}:{found.group(2)}"
            continue
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"vendorfake serve exited with {process.returncode} before it bound:\n{stderr}")
        time.sleep(0.01)
    _stop(process)
    raise RuntimeError(f"vendorfake serve did not announce a port within {timeout_s}s")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a hang
            process.kill()
            process.wait(timeout=5)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
