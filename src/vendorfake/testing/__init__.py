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

import collections
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vendorfake.asgi import FrameworkTripwire

import httpx

from vendorfake.core.kernel.types import Logger
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.webhooks.models import matches_event_type
from vendorfake.core.webhooks.sink import DeliverySink
from vendorfake.registry import RouteInfo, create_unit
from vendorfake.testing.receiver import Delivery, WebhookReceiver, webhook_receiver
from vendorfake.testing.seeds import CloverSeed, SquareSeed, ToastSeed, seed_for
from vendorfake.testing.transport import UnitTransport

__all__ = [
    "CLIENT_TIMEOUT_S",
    "DRAIN_TIMEOUT_S",
    "LOG_LINES",
    "SERVE_COMMAND",
    "CloverSeed",
    "Delivery",
    "Driver",
    "RouteInfo",
    "ServedUnit",
    "SquareSeed",
    "StartedUnit",
    "ToastSeed",
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

CLIENT_TIMEOUT_S = 30.0
"""The HTTP timeout for every client this module builds over a socket
(:func:`served` and :func:`serve_in_thread`). One constant so the two cannot
drift: they briefly did, and the thread-served client's httpx default of 5s
was shorter than a real-clock :meth:`Driver.drain` legitimately takes."""

DRAIN_TIMEOUT_S = 120.0
"""How long :meth:`Driver.drain` waits, overriding the client timeout for
that one call. Sized for the shipped profiles: their retry schedules are
compressed (``time_scale``), and the longest -- Square's eleven attempts,
exhausted -- settles in about fifteen seconds of real time."""
_LISTENING = re.compile(r"listening on http://([^:\s]+):(\d+)")


@dataclass
class Driver:
    """A unit you can talk to, however it was started."""

    vendor: str
    profile: str
    base_url: str
    client: httpx.Client
    seed: SquareSeed | CloverSeed | ToastSeed | None

    # -- reading ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/health"))

    def info(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/info"))

    def _route_table(self) -> list[dict[str, Any]]:
        return list(self._json(self.client.get("/__unit/routes"))["routes"])

    def route_for(self, operation_id: str) -> RouteInfo:
        """The route named ``operation_id``, discovered from ``GET
        /__unit/routes`` -- works over any binding this :class:`Driver`
        happens to be, in-process or served, because it never reaches for a
        ``Unit`` object.

        Raises ``KeyError`` naming every operation id this unit actually
        registers, so a typo is a startup failure that lists the real ones
        rather than a ``KeyError`` with nothing to go on.
        """
        for row in self._route_table():
            if row.get("operation_id") == operation_id:
                return RouteInfo(
                    method=str(row["method"]),
                    path=str(row["path"]),
                    operation_id=operation_id,
                    capability=str(row["capability"]),
                    summary=None if row.get("summary") is None else str(row["summary"]),
                    internal=bool(row.get("internal", False)),
                )
        known = sorted(str(row["operation_id"]) for row in self._route_table() if row.get("operation_id"))
        raise KeyError(f"no route with operation_id {operation_id!r}. Known: {known}")

    def path_for(self, operation_id: str) -> str:
        """``self.route_for(operation_id).path``, for the common case that
        only wants the path template."""
        return self.route_for(operation_id).path

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

        ``event_types`` are checked against the vendor's vocabulary when the
        seed publishes one: the control plane accepts any string, so a Square
        type on a Clover unit would register fine and then never fire -- the
        test passes its setup and fails much later, with nothing to say why.
        Globs the dispatcher honours (``O:*``, ``*``) pass if they match at
        least one published type.
        """
        vocabulary = None if self.seed is None else self.seed.event_types
        if vocabulary is not None:
            unknown = [
                pattern
                for pattern in event_types
                if not any(matches_event_type([pattern], known) for known in vocabulary)
            ]
            if unknown:
                raise ValueError(
                    f"{self.vendor!r} sends none of {unknown}; its event types are {list(vocabulary)} "
                    "(a glob that matches at least one of them, or '*', is accepted)"
                )
        body: dict[str, Any] = {
            "notification_url": notification_url,
            "event_types": list(event_types),
            "signature_key": signature_key,
        }
        if id is not None:
            body["id"] = id
        return self._json(self.client.post("/__unit/webhooks/subscriptions", json=body), expect=(200, 201))

    def drain(self, *, timeout_s: float | None = DRAIN_TIMEOUT_S) -> None:
        """Wait for pending deliveries -- retries included -- to settle, and
        refuse to pretend they did when they did not.

        What ``POST /__unit/webhooks/drain`` actually guarantees is
        **pass-bounded, not "until settled"**: the unit loops at most 500
        passes, each sleeping up to 250 ms on a real clock, so the call
        returns after roughly 125 s of real time *whatever is still
        scheduled*. On the shipped profiles that bound is never reached --
        their retry schedules are compressed by ``time_scale``, and the
        longest cascade (Square's eleven retries to exhaustion) settles in
        about fifteen seconds. On a custom profile with an *uncompressed*
        schedule the unit's drain returns early, deliveries still pending.

        An early return is indistinguishable from settled at the call site,
        so this method checks afterwards and raises ``RuntimeError`` naming
        the still-pending timer rather than letting the next assertion run
        against deliveries that have not happened
        (:meth:`pending_webhook_timers` is the same check, callable on its
        own). For an uncompressed schedule do not drain in real time at all:
        run the unit on a virtual clock (``VENDORFAKE_CLOCK=virtual``) and
        :meth:`advance_clock` past the schedule.

        ``timeout_s`` bounds the HTTP wait for this one call (``None``: no
        HTTP timeout). Against a :func:`unit` client it is not honoured and
        the call blocks until the unit's drain returns -- see
        :class:`~vendorfake.testing.transport.UnitTransport`.
        """
        self._json(self.client.post("/__unit/webhooks/drain", json={}, timeout=timeout_s))
        pending = self.pending_webhook_timers()
        if pending:
            nearest = min(pending, key=lambda timer: float(timer["due_in_ms"]))
            raise RuntimeError(
                f"drain returned with {len(pending)} webhook timer(s) still pending -- the unit's drain is "
                f"pass-bounded and gave up before {nearest['label']!r} (due in {float(nearest['due_in_ms']):.0f} ms). "
                f"This profile's retry schedule is too long to drain in real time: run the unit on a virtual "
                f"clock (VENDORFAKE_CLOCK=virtual) and advance_clock() past the schedule instead."
            )

    def pending_webhook_timers(self) -> list[dict[str, Any]]:
        """Delivery retries still scheduled, as the clock reports them.

        Empty means settled. The filter mirrors the dispatcher's own timer
        labelling (``webhook:...`` / ``webhook-retry:...``); other machinery
        may hold timers of its own, and those are not deliveries.
        """
        timers = self.info()["clock"]["pending_timers"]
        return [timer for timer in timers if str(timer.get("label", "")).startswith("webhook")]

    # -- state and faults ----------------------------------------------------

    def reset(self) -> dict[str, Any]:
        """Back to the seed scenario -- and only the scenario.

        Everything a test created goes, **including subscribers registered
        through the control plane or the vendor API**: the store is cleared
        and re-hydrated, and only the seed document's and the profile's
        subscribers are re-inserted. Call :meth:`subscribe` again after a
        reset, or subscribe after the reset in the first place -- a
        subscription hoisted above a per-test reset delivers nothing, and
        that reads exactly like a broken handler.
        """
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
    #: The counter behind ``framework_answered`` in ``/__unit/health``. Wired
    #: at construction and handed to :func:`serve_in_thread`'s application,
    #: because a counter wired at neither end reports a literal 0 -- the
    #: regression tests/conformance/harness.py records as a real incident.
    tripwire: FrameworkTripwire = field(kw_only=True)


@dataclass
class ServedUnit(Driver):
    """From :func:`served`: a child process the block will stop."""

    process: subprocess.Popen[str] = field(kw_only=True)
    _output: _ChildOutput = field(kw_only=True, repr=False)

    @property
    def pid(self) -> int:
        return self.process.pid

    def logs(self) -> list[str]:
        """The child's most recent output, stdout and stderr interleaved.

        Bounded (:data:`LOG_LINES`): a chatty child on ``--log-level debug``
        keeps writing for the life of the test, and the pipe must be read
        continuously or the child blocks on it once 64 KB is buffered. The
        reader thread that prevents that keeps the tail here, which is the
        part a failing test wants to print anyway.
        """
        return self._output.tail()


def _seed_of(built: Unit) -> SquareSeed | CloverSeed | ToastSeed | None:
    return seed_for(built.name, built.context.config.vendor_config)


@contextmanager
def unit(
    vendor: str,
    profile: str | None = None,
    *,
    capabilities: Sequence[str] | None = None,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
    seed: int | None = None,
) -> Iterator[StartedUnit]:
    """A unit in this process, stopped however the block ends.

    ``profile`` defaults to ``None``, which resolves to ``full`` -- the same
    default it has always had -- unless ``capabilities`` is given instead;
    see :func:`~vendorfake.registry.create_unit` for exactly how a
    capability request resolves to a profile, and note that supplying both
    ``profile`` and ``capabilities`` is a ``ValueError``.

    ``sink`` defaults to real HTTP delivery, so a subscribed
    :func:`webhook_receiver` sees signed bytes; pass
    ``vendorfake.core.webhooks.sink.MemorySink()`` to capture in memory
    instead. ``env`` is the ``VENDORFAKE_*`` layer, empty by default -- the
    process environment is never read here, so one test's variables cannot
    change another test's profile.

    **Ids are deterministic per unit.** Two units on the same profile mint the
    same order ids, tokens and codes in the same order, because each starts
    its id stream from the profile's seed -- that is what makes an id
    assertion stable across runs. Their stores are separate, so nothing
    collides; but a test that compares ids *between* two units, or keeps
    ids across a unit's lifetime as if they were unique, will be surprised.
    ``seed`` restarts the stream (and the fault engine's RNG) from another
    number, for the rare test that needs two units to diverge; it is the
    ``VENDORFAKE_CHAOS_SEED`` layer, so an explicit ``env`` entry wins.
    """
    environ: dict[str, str] = {} if seed is None else {"VENDORFAKE_CHAOS_SEED": str(seed)}
    environ.update(env or {})
    # This import brings the web framework in (FrameworkTripwire lives in
    # vendorfake.asgi), and it is paid deliberately: `framework_answered` must
    # be wired at unit construction or `/__unit/health` reports a literal 0
    # rather than a measurement. No *application* is built until
    # `serve_in_thread` asks for one.
    from vendorfake.asgi import FrameworkTripwire

    tripwire = FrameworkTripwire()
    built = create_unit(
        vendor=vendor,
        profile=profile,
        capabilities=capabilities,
        env=environ,
        sink=sink,
        logger=JsonLogger("warn") if logger is None else logger,
        framework_answered=tripwire.get,
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
                tripwire=tripwire,
            )
    finally:
        built.stop()


@contextmanager
def serve_in_thread(started: StartedUnit, *, host: str = "127.0.0.1", port: int = 0) -> Iterator[Driver]:
    """A real server in front of ``started``'s unit, on a background thread.

    Yields a second :class:`Driver` onto the *same* unit, so state written
    through either client is visible through the other.

    The application is handed the unit's own tripwire, so
    ``framework_answered`` in ``/__unit/health`` is a measurement here: a
    request the framework answers instead of the unit moves it.
    """
    from vendorfake.asgi import create_app
    from vendorfake.asgi import serve_in_thread as serve_app

    with (
        serve_app(create_app(started.unit, tripwire=started.tripwire), host=host, port=port) as base_url,
        httpx.Client(base_url=base_url, timeout=CLIENT_TIMEOUT_S) as client,
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

    Both pipes are read on a daemon thread for the life of the child, so a
    child that logs more than the pipe buffers cannot block mid-test, and
    ``timeout_s`` is a real deadline: a child that never announces -- wedged,
    or not vendorfake at all -- is stopped and reported rather than waited
    on forever.
    """
    argv = [
        *SERVE_COMMAND,
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
    output = _ChildOutput(process)
    try:
        base_url = _wait_for_announcement(process, output, timeout_s)
        with httpx.Client(base_url=base_url, timeout=CLIENT_TIMEOUT_S) as client:
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
                _output=output,
            )
    finally:
        _stop(process)
        output.join()


SERVE_COMMAND: tuple[str, ...] = (sys.executable, "-m", "vendorfake", "serve")
"""What :func:`served` runs, before the flags. A module attribute so a test
of the startup deadline can substitute a child that never announces."""

LOG_LINES = 500
"""How much of a child's output :meth:`ServedUnit.logs` keeps."""


class _ChildOutput:
    """Reads a child's stdout and stderr to the end, on threads.

    Every line goes into one bounded tail (for :meth:`ServedUnit.logs`), and
    stdout lines also go onto a queue so that :func:`_wait_for_announcement`
    can wait for the port with a timeout -- ``readline()`` on the pipe itself
    has none, and a child that prints nothing would have held the caller for
    as long as it lived.
    """

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._process = process
        self._tail: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
        self._lock = threading.Lock()
        self.stdout: queue.Queue[str | None] = queue.Queue()
        self._threads = [
            threading.Thread(target=self._pump, args=(process.stdout, self.stdout), daemon=True),
            threading.Thread(target=self._pump, args=(process.stderr, None), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _pump(self, stream: Any, sink: queue.Queue[str | None] | None) -> None:
        if stream is None:  # pragma: no cover - Popen was given PIPE for both
            return
        for line in stream:
            with self._lock:
                self._tail.append(line.rstrip("\n"))
            if sink is not None:
                sink.put(line)
        if sink is not None:
            sink.put(None)

    def tail(self) -> list[str]:
        with self._lock:
            return list(self._tail)

    def join(self) -> None:
        for thread in self._threads:
            thread.join(timeout=SHUTDOWN_TIMEOUT_S)
        for stream in (self._process.stdout, self._process.stderr):
            if stream is not None:
                stream.close()


def _wait_for_announcement(process: subprocess.Popen[str], output: _ChildOutput, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop(process)
            raise RuntimeError(
                f"vendorfake serve did not announce a port within {timeout_s}s; last output:\n"
                + "\n".join(output.tail()[-20:])
            )
        try:
            line = output.stdout.get(timeout=min(remaining, 0.25))
        except queue.Empty:
            continue
        if line is None:
            # stdout closed: the child exited without announcing.
            process.wait(timeout=SHUTDOWN_TIMEOUT_S)
            raise RuntimeError(
                f"vendorfake serve exited with {process.returncode} before it bound:\n" + "\n".join(output.tail()[-20:])
            )
        found = _LISTENING.search(line)
        if found is not None:
            return f"http://{found.group(1)}:{found.group(2)}"


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a hang
            process.kill()
            process.wait(timeout=5)
