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
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, overload

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vendorfake.asgi import FrameworkTripwire

import httpx

from vendorfake.core.config.models import UnmatchedPolicy
from vendorfake.core.control.plane import DEFAULT_REQUEST_LIMIT
from vendorfake.core.kernel.types import Logger
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.webhooks.models import matches_event_type
from vendorfake.core.webhooks.sink import DeliverySink
from vendorfake.registry import create_unit, resolve_vendor
from vendorfake.testing.receiver import Delivery, WebhookReceiver, webhook_receiver
from vendorfake.testing.seeds import CloverSeed, Credentials, Seed, SquareSeed, ToastSeed, seed_for
from vendorfake.testing.transport import UnitTransport, UnmatchedRequest

__all__ = [
    "CLIENT_TIMEOUT_S",
    "DEFAULT_REQUEST_LIMIT",
    "DRAIN_TIMEOUT_S",
    "LOG_LINES",
    "NO_SEED_HINT",
    "SERVE_COMMAND",
    "CloverSeed",
    "Credentials",
    "Delivery",
    "Driver",
    "Seed",
    "SeedT",
    "ServedUnit",
    "SquareSeed",
    "StartedUnit",
    "ToastSeed",
    "UnitTransport",
    "UnmatchedRequest",
    "WebhookReceiver",
    "serve_in_thread",
    "served",
    "unit",
    "webhook_receiver",
]

SeedT = TypeVar("SeedT", bound=Seed, covariant=True)
"""Which vendor's seed a driver carries.

A type variable rather than a union because the union does not narrow: the
vendor string tells a reader which seed is in hand, and before this it told a
type checker nothing, so every typed consumer wrote an ``isinstance`` per
vendor to get at a field. :func:`unit` and :func:`served` are overloaded on
the vendor literal and bind this; a vendor that is a plain ``str`` -- a
parametrized test, or a vendor from the entry-point group -- binds
:class:`~vendorfake.testing.seeds.Seed`, which is what every seed has in
common.

**Covariant**, so that ``StartedUnit[SquareSeed]`` is a
``StartedUnit[Seed]``. That is not decoration: the literal overloads and the
``str`` fallback overlap -- every ``Literal["square"]`` is also a ``str`` --
and with an invariant parameter the narrow overload's return type is not
assignable to the broad one's, which pyright reports as an overlapping
overload with an incompatible return. Covariance is technically unsound for a
mutable attribute, and the hole is real, not theoretical: a checker accepts
``def reassign(d: Driver[Seed], other: Seed) -> None: d.seed = other`` called
with a ``StartedUnit[SquareSeed]`` and a ``ToastSeed``, and afterwards
``square.seed.merchant_id`` still type-checks and raises ``AttributeError`` at
runtime. Nothing in vendorfake reassigns ``seed`` -- it is written once, at
construction, and read everywhere else -- but ``Driver`` is a plain mutable
``@dataclass`` handed to consumers, so avoiding the reassignment is *their*
responsibility, not a guarantee this module makes. The name keeps no ``_co``
suffix because it is public API and the variance is a property of the
driver, not something a consumer spells.
"""

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
class Driver(Generic[SeedT]):
    """A unit you can talk to, however it was started.

    Generic in its seed. ``seed`` used to be
    ``SquareSeed | CloverSeed | ToastSeed | None``, which meant that reading
    one field of it took an ``isinstance`` ladder *and* a ``None`` guard --
    per vendor, in every consumer, for a value that is never actually absent
    and whose type the caller already named in ``unit("square")``. The
    parameter carries that name through, and the ``None`` is gone: a vendor
    with no seed is refused where the unit is built (:func:`unit`) rather
    than handed back as an ``Optional`` for everyone downstream to guard.
    """

    vendor: str
    profile: str
    base_url: str
    client: httpx.Client
    seed: SeedT

    # -- reading ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/health"))

    def info(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/info"))

    def deliveries(self) -> list[dict[str, Any]]:
        """Every webhook delivery attempt the unit made, oldest first."""
        return list(self._json(self.client.get("/__unit/webhooks/deliveries"))["deliveries"])

    # -- what was called -----------------------------------------------------

    def requests(
        self,
        *,
        operation_id: str | None = None,
        route: str | None = None,
        unmatched: bool | None = None,
        limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> list[dict[str, Any]]:
        """What the code under test called, **newest first**.

        The counterpart to :meth:`deliveries`, and the answer to a question the
        journal cannot be asked: the journal records committed mutations, so a
        read, a 4xx and a request that matched nothing leave no trace in it.

        ``operation_id`` is the stable name a route publishes (``ObtainToken``,
        ``CreateOrder``; ``GET /__unit/routes`` lists them) and is the filter to
        prefer, because it survives a vendor moving a path. ``route`` matches
        the template form, ``"POST /v2/orders"``. ``unmatched=True`` narrows to
        the calls no route answered, each carrying the routes it nearly asked
        for. Control-plane traffic -- including this call -- is never recorded.

        Bodies and headers are deliberately absent from a record; see
        :class:`~vendorfake.core.kernel.types.RequestRecord`.
        """
        query: dict[str, str] = {"limit": str(limit)}
        if operation_id is not None:
            query["operation_id"] = operation_id
        if route is not None:
            query["route"] = route
        if unmatched is not None:
            query["unmatched"] = "true" if unmatched else "false"
        return list(self._json(self.client.get("/__unit/requests", params=query))["requests"])

    def clear_requests(self) -> int:
        """Forget every recorded request, returning how many there were.

        State is untouched: this is not :meth:`reset`. Use it to draw a line
        under setup so that an ``assert_called`` afterwards counts only what
        the part under test did.
        """
        return int(self._json(self.client.delete("/__unit/requests"))["cleared"])

    def assert_called(
        self,
        operation_id: str,
        *,
        times: int | None = None,
        at_least: int | None = None,
    ) -> list[dict[str, Any]]:
        """Assert an operation was called, and say what *was* called if not.

        With neither argument: at least once. ``times`` is exact, ``at_least``
        is a floor, and passing both is a programming error rather than a
        conjunction -- one of them is always redundant and guessing which would
        make the assertion mean different things to reader and runner.

        The failure message lists every operation the unit did see, with
        counts, in the spirit of pytest-httpx's "No response can be found for
        X amongst:". A bare "expected 2, got 1" sends the reader to the log by
        hand; the usual causes -- a typo'd path, a capability switched off, a
        request that never left the code under test -- are all visible in that
        list.

        Returns the matching records, newest first, so a test can go on to
        assert on the status or the fault of the call it just proved happened.
        """
        if times is not None and at_least is not None:
            raise ValueError("pass times= or at_least=, not both: one of the two is always redundant")
        capacity = self._request_capacity()
        if capacity == 0:
            # Refused rather than answered "saw 0", which would be a passing
            # assert_called(times=0) and a failing everything else, for a unit
            # that was never recording in the first place.
            raise AssertionError(
                f"vendorfake: the request log is switched off for this unit (requests.capacity is 0 "
                f"in profile {self.profile!r}), so nothing can be asserted about what was called."
            )
        floor = 1 if (times is None and at_least is None) else at_least
        found = self.requests(operation_id=operation_id, limit=capacity)
        count = len(found)
        if (times is not None and count != times) or (floor is not None and count < floor):
            wanted = f"exactly {times}" if times is not None else f"at least {floor}"
            raise AssertionError(
                f"vendorfake: expected {wanted} call(s) to {operation_id!r} on {self.vendor} "
                f"(profile {self.profile!r}), saw {count}.\n" + self._what_was_called(capacity)
            )
        return found

    def _request_capacity(self) -> int:
        """The log's own bound, so a count is over everything it holds.

        Asked rather than assumed: a profile may have raised or lowered it, and
        an assertion that counted the first hundred records of a log holding
        ten thousand would be quietly wrong in exactly the long run where it
        mattered.
        """
        return int(self._json(self.client.get("/__unit/requests", params={"limit": "1"}))["capacity"])

    def _what_was_called(self, capacity: int) -> str:
        records = self.requests(limit=capacity)
        if not records:
            return (
                "Nothing was called at all. If this unit was reset (or clear_requests() ran) after the "
                "code under test, the calls are gone; the log is cleared by reset()."
            )
        counts: dict[str, int] = {}
        for record in records:
            name = str(record.get("operation_id") or f"{record['method']} {record['path']} (no route matched)")
            counts[name] = counts.get(name, 0) + 1
        lines = [f"What was called ({len(records)} request(s) recorded):"]
        lines.extend(
            f"  {count:>3}  {name}" for name, count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))
        )
        return "\n".join(lines)

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

        ``event_types`` are checked against the vendor's vocabulary: the
        control plane accepts any string, so a Square type on a Clover unit
        would register fine and then never fire -- the test passes its setup
        and fails much later, with nothing to say why. Globs the dispatcher
        honours (``O:*``, ``*``) pass if they match at least one published
        type.

        The check used to be conditional on the seed being present. It is not
        any more, because the seed is not optional any more; a driver with no
        vocabulary to check against no longer exists.
        """
        vocabulary = self.seed.event_types
        unknown = [
            pattern for pattern in event_types if not any(matches_event_type([pattern], known) for known in vocabulary)
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
class StartedUnit(Driver[SeedT]):
    """From :func:`unit`: the unit itself is reachable for anything the
    control plane does not cover.

    ``StartedUnit[SquareSeed]`` is what ``unit("square")`` yields. Written
    bare -- ``StartedUnit`` -- it is ``StartedUnit[Any]``, which is what a
    v0.1.0 fixture annotation already says and keeps working.
    """

    unit: Unit = field(kw_only=True)
    #: The counter behind ``framework_answered`` in ``/__unit/health``. Wired
    #: at construction and handed to :func:`serve_in_thread`'s application,
    #: because a counter wired at neither end reports a literal 0 -- the
    #: regression tests/conformance/harness.py records as a real incident.
    tripwire: FrameworkTripwire = field(kw_only=True)


@dataclass
class ServedUnit(Driver[SeedT]):
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


NO_SEED_HINT = (
    "vendorfake ships a seed for square, clover and toast. A vendor from the "
    "'vendorfake.vendors' entry-point group has none here, so its ids, tokens and "
    "application credentials are not readable through .seed -- read them from that "
    "distribution's own constants instead, and drive the unit with create_unit()."
)
"""What a caller can actually do about a vendor with no seed.

Split out so the message is one string and a test can assert on it without
copying the prose.
"""


def _require_seed(vendor: str, profile: str, found: SquareSeed | CloverSeed | ToastSeed | None) -> Seed:
    """``found``, or a refusal that says why there is none.

    ``seed`` used to be handed back as ``None`` for any vendor
    :func:`~vendorfake.testing.seeds.seed_for` does not describe, and every
    consumer paid for that with a guard on a value that is present for all
    three shipped vendors. The absence is real but it is a property of the
    *vendor*, not of a call -- so it is answered once, at the moment the unit
    is started, where the vendor and profile are still in hand to name.
    """
    if found is None:
        raise LookupError(f"vendor {vendor!r} (profile {profile!r}) publishes no seed. {NO_SEED_HINT}")
    return found


@overload
def unit(
    vendor: Literal["square"],
    profile: str = ...,
    *,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
) -> AbstractContextManager[StartedUnit[SquareSeed]]: ...


@overload
def unit(
    vendor: Literal["clover"],
    profile: str = ...,
    *,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
) -> AbstractContextManager[StartedUnit[CloverSeed]]: ...


@overload
def unit(
    vendor: Literal["toast"],
    profile: str = ...,
    *,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
) -> AbstractContextManager[StartedUnit[ToastSeed]]: ...


@overload
def unit(
    vendor: str,
    profile: str = ...,
    *,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
) -> AbstractContextManager[StartedUnit[Seed]]: ...


def unit(
    vendor: str,
    profile: str = "full",
    *,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
    seed: int | None = None,
    unmatched: UnmatchedPolicy | None = None,
) -> AbstractContextManager[StartedUnit[Any]]:
    """A unit in this process, stopped however the block ends.

    The overloads above are the whole point of the vendor argument being a
    literal: ``unit("clover")`` yields a ``StartedUnit[CloverSeed]``, so
    ``started.seed.merchant_id`` type-checks and ``started.seed.tea_item_id``
    does not. A vendor that is a plain ``str`` -- a parametrized test, or one
    discovered through the entry-point group -- falls to the last overload
    and yields ``StartedUnit[Seed]``: the fields every vendor has, and no
    guessing.

    There is one implementation; the overloads are declarations. It delegates
    to a private generator rather than wearing ``@contextmanager`` itself,
    because a decorated implementation and a set of overloads do not compose
    in either checker. The object handed back is the same
    ``contextlib`` context manager it always was; only its declared type is
    ``AbstractContextManager``.
    """
    return _unit(vendor, profile, sink=sink, env=env, logger=logger, seed=seed, unmatched=unmatched)


@contextmanager
def _unit(
    vendor: str,
    profile: str = "full",
    *,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
    seed: int | None = None,
    unmatched: UnmatchedPolicy | None = None,
) -> Iterator[StartedUnit[Seed]]:
    """The body of :func:`unit`. See that function for the contract.

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

    **A request no route matches raises**
    :class:`~vendorfake.testing.transport.UnmatchedRequest` here, which is a
    change from v0.1. In process this object is a test double, and a wrong path
    is a test-authoring mistake that should fail the test that made it rather
    than arrive as the vendor's 404 several assertions later. Pass
    ``unmatched="vendor-404"`` for a test that deliberately calls an unmodelled
    path -- a 404-handling rehearsal, say -- or set ``unmatched.policy`` in the
    profile (or ``VENDORFAKE_UNMATCHED``) to change it for a whole suite.
    :func:`served` and :func:`serve_in_thread` never raise: a served unit
    stands in for the vendor, and there is no caller stack across a socket to
    raise into. Either way the diagnosis is on the response, in
    ``Vendorfake-Near-Miss``.
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
        env=environ,
        sink=sink,
        logger=JsonLogger("warn") if logger is None else logger,
        framework_answered=tripwire.get,
    )
    try:
        # Before the client, so a vendor with no seed is refused with the unit
        # already stopped by the `finally` rather than left running behind a
        # half-built driver. Named with `built.name` and the config's own
        # `profile`, not the raw arguments above: `seed_for` is keyed on the
        # resolved vendor, and a registry alias or a profile default (from
        # `env`) can make the resolved values differ from what the caller
        # spelled, in which case the refusal should name what was actually
        # looked up.
        resolved_seed = _require_seed(
            built.name, built.context.config.profile, seed_for(built.name, built.context.config.vendor_config)
        )
        transport = UnitTransport(built, unmatched=unmatched)
        with httpx.Client(transport=transport, base_url=IN_PROCESS_BASE_URL) as client:
            yield StartedUnit(
                vendor=built.name,
                profile=built.context.config.profile,
                base_url=IN_PROCESS_BASE_URL,
                client=client,
                seed=resolved_seed,
                unit=built,
                tripwire=tripwire,
            )
    finally:
        built.stop()


@contextmanager
def serve_in_thread(started: StartedUnit[SeedT], *, host: str = "127.0.0.1", port: int = 0) -> Iterator[Driver[SeedT]]:
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


@overload
def served(
    vendor: Literal["square"],
    profile: str = ...,
    *,
    port: int = ...,
    host: str = ...,
    log_level: str = ...,
    timeout_s: float = ...,
) -> AbstractContextManager[ServedUnit[SquareSeed]]: ...


@overload
def served(
    vendor: Literal["clover"],
    profile: str = ...,
    *,
    port: int = ...,
    host: str = ...,
    log_level: str = ...,
    timeout_s: float = ...,
) -> AbstractContextManager[ServedUnit[CloverSeed]]: ...


@overload
def served(
    vendor: Literal["toast"],
    profile: str = ...,
    *,
    port: int = ...,
    host: str = ...,
    log_level: str = ...,
    timeout_s: float = ...,
) -> AbstractContextManager[ServedUnit[ToastSeed]]: ...


@overload
def served(
    vendor: str,
    profile: str = ...,
    *,
    port: int = ...,
    host: str = ...,
    log_level: str = ...,
    timeout_s: float = ...,
) -> AbstractContextManager[ServedUnit[Seed]]: ...


def served(
    vendor: str,
    profile: str = "full",
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    log_level: str = "error",
    timeout_s: float = STARTUP_TIMEOUT_S,
) -> AbstractContextManager[ServedUnit[Any]]:
    """``vendorfake serve`` in a child process, with its URL.

    Overloaded on the vendor literal for the same reason :func:`unit` is: the
    child serves the vendor it was told to, so the seed's type is knowable at
    the call site and there is no reason to make a consumer prove it with an
    ``isinstance``. Delegates to a private generator; see :func:`unit`.
    """
    return _served(vendor, profile, port=port, host=host, log_level=log_level, timeout_s=timeout_s)


@contextmanager
def _served(
    vendor: str,
    profile: str = "full",
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    log_level: str = "error",
    timeout_s: float = STARTUP_TIMEOUT_S,
) -> Iterator[ServedUnit[Seed]]:
    """The body of :func:`served`. See that function for the contract.

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
    # Resolved and refused before the child is spawned: `seed_for` is a pure
    # branch on the vendor's canonical name, and `resolve_vendor` is the same
    # registry lookup `create_unit` pays for internally, so neither needs a
    # running unit. Paying for a subprocess that boots, announces its port and
    # answers a health check only to be told the vendor has no seed wastes the
    # startup on every call in a suite that does this per test, and points the
    # traceback at a line inside a connected client rather than at the vendor
    # argument that is actually wrong. `profile` has nothing left to resolve
    # here -- unlike `unit()`, `served()` has no `env` layer that could move it
    # away from what the caller spelled, so the argument already is the
    # resolved value.
    resolved_name = resolve_vendor(vendor).name
    resolved_seed = _require_seed(resolved_name, profile, seed_for(resolved_name, {}))
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
                seed=resolved_seed,
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
