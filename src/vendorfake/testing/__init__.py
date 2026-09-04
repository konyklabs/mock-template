"""``vendorfake.testing`` -- the fixtures a consumer's test suite reaches for.

FOR: getting an integration team from ``pip install vendorfake`` to a green
test in minutes, without learning how units, profiles, bindings or the control
plane fit together. Three ways to hold a unit, one shape once you have it:

:func:`unit`
    A unit in this process, driven through an ``httpx.Client`` with no socket.
    Fast enough to build per test. Webhooks still go out over real HTTP to
    whatever URL is subscribed, so a :func:`webhook_receiver` on loopback sees
    signed deliveries exactly as a served unit would send them. An async
    consumer reaches for :attr:`StartedUnit.async_client` on the same object --
    same unit, same transport, no second wiring.

:func:`async_unit`
    The same thing as an ``async with``, for a consumer whose fixtures are
    ``async def``. It delegates to :func:`unit`, so nothing about how a unit is
    built has a second description; what it adds is awaiting the async client's
    close on the way out.

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

import asyncio
import collections
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, overload

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vendorfake.asgi import FrameworkTripwire

import httpx

from vendorfake import registry
from vendorfake.core.config.models import ResolvedConfig, UnmatchedPolicy
from vendorfake.core.config.profile import ENV_VENDOR_PREFIX, load_profile
from vendorfake.core.control.plane import DEFAULT_REQUEST_LIMIT
from vendorfake.core.kernel.types import Logger, UnitError, UnitErrorKind, VendorDefinition
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.logging import JsonLogger
from vendorfake.core.util.json import canonical_json
from vendorfake.core.webhooks.models import matches_event_type
from vendorfake.core.webhooks.sink import DeliverySink
from vendorfake.registry import RouteInfo, create_unit
from vendorfake.testing.receiver import Delivery, WebhookReceiver, webhook_receiver
from vendorfake.testing.seeds import (
    CloverSeed,
    CloverSeedOverlay,
    Credentials,
    Seed,
    SeedOverlay,
    SquareSeed,
    SquareSeedOverlay,
    ToastSeed,
    ToastSeedOverlay,
    Token,
    seed_collections_for,
    seed_for,
)
from vendorfake.testing.transport import UnitTransport, UnmatchedRequest, checked_unmatched

__all__ = [
    "CLIENT_TIMEOUT_S",
    "DEFAULT_REQUEST_LIMIT",
    "DRAIN_TIMEOUT_S",
    "LOG_LINES",
    "NO_SEED_HINT",
    "SERVE_COMMAND",
    "ClockInfo",
    "CloverSeed",
    "CloverSeedOverlay",
    "Credentials",
    "Delivery",
    "Driver",
    "RouteInfo",
    "Seed",
    "SeedOverlay",
    "SeedT",
    "ServedUnit",
    "SquareSeed",
    "SquareSeedOverlay",
    "StartedUnit",
    "ToastSeed",
    "ToastSeedOverlay",
    "Token",
    "UnitTransport",
    "UnmatchedRequest",
    "WebhookReceiver",
    "async_unit",
    "checked_unmatched",
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
"""The HTTP timeout for every client this module builds -- over a socket
(:func:`served` and :func:`serve_in_thread`) and in process
(:func:`unit`'s :attr:`Driver.client`, :attr:`StartedUnit.async_client`).
One constant so none of them drift apart: the socket clients briefly did,
and the thread-served client's httpx default of 5s was shorter than a
real-clock :meth:`Driver.drain` legitimately takes. The in-process clients
matter for the same reason since the `timeout` chaos fault started raising
``httpx.ReadTimeout`` for a `delay_ms` past this threshold instead of
waiting: left unset, httpx's own default is 5s, not this constant, and a
consumer's rule armed past 5s but under 30s would raise where the built-in
client is documented to answer with a 504 instead."""

DRAIN_TIMEOUT_S = 120.0
"""How long :meth:`Driver.drain` waits, overriding the client timeout for
that one call. Sized for the shipped profiles: their retry schedules are
compressed (``time_scale``), and the longest -- Square's eleven attempts,
exhausted -- settles in about fifteen seconds of real time."""
_LISTENING = re.compile(r"listening on http://([^:\s]+):(\d+)")


@dataclass(frozen=True, slots=True)
class ClockInfo:
    """The unit's clock, as :meth:`Driver.clock` reads it off ``/__unit/info``."""

    now: datetime
    mode: Literal["real", "virtual"]


def _clock_start_env_value(clock_start: datetime | str) -> str:
    """``VENDORFAKE_CLOCK_START``'s value, from either spelling :func:`unit`
    and :func:`served` accept.

    A naive ``datetime`` raises rather than being read as local time: a
    ``clock_start`` two developers on two machines both wrote as
    ``datetime(2026, 1, 1)`` must pin the same instant, and reading it as
    local time would make it pin a different one on each machine -- silently,
    since nothing about a naive ``datetime`` says which machine wrote it.
    """
    if isinstance(clock_start, str):
        return clock_start
    if clock_start.tzinfo is None:
        raise ValueError(
            f"clock_start={clock_start!r} has no timezone. A naive datetime has no defined instant across "
            "machines; pass a timezone-aware one (e.g. datetime(..., tzinfo=UTC)) or an RFC 3339 string."
        )
    return clock_start.isoformat()


def _seed_overlay_env_value(seed_overlay: Mapping[str, Any] | str | os.PathLike[str]) -> str:
    """``VENDORFAKE_SEED_OVERLAY``'s value, from either spelling the three
    bindings accept.

    A mapping becomes the document itself, encoded as canonical JSON -- keys
    sorted, no whitespace -- so that the value carried into the unit is the
    same string the digest at ``GET /__unit/info`` is computed from, and two
    callers who wrote the same overlay with their keys in a different order
    produce one digest rather than two. A ``str`` or ``os.PathLike`` becomes
    the path, unchanged; the loader reads a value starting with ``{`` as
    inline JSON and anything else as a path, which is why an inline document
    can never be mistaken for a filename.

    ``os.fspath`` rather than ``str()``: a ``Path`` stringifies the same way,
    but any other ``os.PathLike`` (a ``TemporaryDirectory``-adjacent wrapper,
    a consumer's own type) would stringify to its ``repr`` and reach the
    loader as a filename nothing could open.
    """
    if isinstance(seed_overlay, Mapping):
        return canonical_json({str(key): value for key, value in seed_overlay.items()})
    return os.fspath(seed_overlay)


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

    def clock(self) -> ClockInfo:
        """The unit's clock right now: its mode, and its current instant.

        Reads ``/__unit/info``, so this is another request against the unit
        like :meth:`health` and :meth:`deliveries` -- it advances nothing,
        real or virtual.
        """
        payload = self.info()["clock"]
        return ClockInfo(now=datetime.fromisoformat(str(payload["now"])), mode=payload["mode"])

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

        **This is what makes one unit shareable across tests.** A vendor with
        single-use or rotating state (Clover retires a refresh token the
        moment it is used; every vendor's minted tokens and created orders
        accumulate) hands the *second* test to touch that state a failure
        unrelated to what it tests -- and under random ordering, which test
        that is changes run to run. A session-scoped :func:`served` or
        :func:`unit` therefore needs ``reset()`` in a per-test fixture, with
        :meth:`reset_chaos` beside it (rules are the one thing a reset leaves
        armed); the request log and the journal are cleared by the reset
        itself, so :meth:`clear_requests` is for drawing a line *without*
        one. A virtual clock is not rewound -- see the recipe, "Sharing one
        unit across tests" in ``docs/concepts/chaos-rules-and-faults.md``.

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
    #: The transport behind :attr:`Driver.client`, kept so :attr:`async_client`
    #: can share it rather than build a second one. Optional so that a caller
    #: constructing a ``StartedUnit`` by hand -- there are none here, but the
    #: type is public -- is not broken by this field; that path builds its own.
    _transport: UnitTransport | None = field(default=None, kw_only=True, repr=False)
    _async_client: httpx.AsyncClient | None = field(default=None, kw_only=True, repr=False)

    @property
    def async_client(self) -> httpx.AsyncClient:
        """An ``httpx.AsyncClient`` onto the same unit, built on first access.

        FOR: the async consumer -- a service that injects an ``AsyncClient``
        and whose fixtures are ``async def``. Without this they re-implement
        ASGI wiring per vendor against ``vendorfake.asgi``, which is internal
        and not a supported surface.

        Same base URL and the same transport instance as :attr:`client`, so the
        two are interchangeable views of one unit: a request through either is
        the same call into :meth:`Unit.handle`, and state written through one
        is visible through the other immediately, with no socket in between.

        Lazy, because building it costs an object that most tests never touch,
        and because a fixture that constructed one eagerly would create it on
        whatever loop happened to be running at fixture time. This transport
        binds to no loop at all, but the habit is worth not forming.
        """
        if self._async_client is None:
            transport = self._transport if self._transport is not None else UnitTransport(self.unit)
            self._async_client = httpx.AsyncClient(
                transport=transport, base_url=self.base_url, timeout=CLIENT_TIMEOUT_S
            )
        return self._async_client

    async def aclose(self) -> None:
        """Close the async client if one was built. Safe to call twice.

        :func:`async_unit` awaits this on the way out. A test that reached for
        :attr:`async_client` under the *synchronous* :func:`unit` may call it
        too, but does not have to -- see :func:`_release_async_client`.

        The reference is kept rather than dropped, so :attr:`async_client`
        keeps answering with the closed client instead of quietly building a
        fresh one. A request through it then raises, which is the right answer
        for a block that has already ended; handing back a working client would
        make "closed on exit" untrue for the one caller who noticed.
        """
        if self._async_client is not None:
            await self._async_client.aclose()


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
    "'vendorfake.vendors' entry-point group publishes its own by implementing "
    "vendorfake.core.kernel.types.SeedingVendor -- a seed(vendor_config) method "
    "returning an object with credentials, auth, read_only_auth and event_types. "
    "This one does not, so read its ids and tokens from that distribution's own "
    "constants instead, and drive the unit with create_unit()."
)
"""What a caller can actually do about a vendor with no seed.

Split out so the message is one string and a test can assert on it without
copying the prose.

It names the hook first because that is the fix the vendor's author can make
once, for everyone; ``create_unit()`` is what a consumer of a vendor they do
not control has to fall back to. The earlier wording offered only the second,
which was the whole of the answer before the hook existed.
"""


def _require_seed(vendor: str, profile: str, found: Seed | None) -> Seed:
    """``found``, or a refusal that says why there is none.

    ``seed`` used to be handed back as ``None`` for any vendor
    :func:`~vendorfake.testing.seeds.seed_for` does not describe, and every
    consumer paid for that with a guard on a value that is present for all
    three shipped vendors. The absence is real but it is a property of the
    *vendor*, not of a call -- so it is answered once, at the moment the unit
    is started, where the vendor and profile are still in hand to name.

    ``found`` is typed :class:`~vendorfake.testing.seeds.Seed`, not the union
    of the three built-in seed types, because ``seed_for`` now also answers
    from a vendor's own
    :class:`~vendorfake.core.kernel.types.SeedingVendor` hook and a
    third-party seed is not one of those three. The per-vendor narrowing a
    consumer sees is unaffected: it comes from ``unit()``'s overloads on the
    vendor literal, not from this helper.
    """
    if found is None:
        raise LookupError(f"vendor {vendor!r} (profile {profile!r}) publishes no seed. {NO_SEED_HINT}")
    return found


def _refuse_a_seed_bound_overlay(
    vendor: str,
    profile: str,
    config: ResolvedConfig,
    definition: VendorDefinition | None,
) -> None:
    """Refuse an overlay that would make ``.seed`` describe a different unit.

    THE HOLE THIS CLOSES, and why it is a refusal rather than a fix. The seed
    handed back on :class:`StartedUnit` and :class:`ServedUnit` is built by
    :func:`~vendorfake.testing.seeds.seed_for` out of the vendor's own module
    constants and the profile's ``vendor`` block -- never out of the seed
    document that was loaded. That is fine while the document *is* the
    shipped one. An overlay naming the collection those constants are the
    values of breaks it: the store hydrates the overlaid credentials and
    ``.seed.auth`` still carries the shipped bearer, so every request made
    the documented way answers 401, and nothing in the response, the log or
    the traceback mentions the overlay. Review measured exactly that, on
    ``served()`` and on ``unit()`` alike.

    ``served(env={"VENDORFAKE_SEED": ...})`` is refused three screens down
    for the identical reason and in the identical family of message; this is
    that refusal for the overlay, extended to ``unit()``, which has the same
    hole and no such guard.

    Making ``.seed`` *follow* the merged document instead would be the better
    answer and is a larger change than an overlay: every vendor's seed would
    have to be read out of its own document rather than named as a constant,
    and the document's shape would become part of what a fixture promises.
    Until that happens the honest answer is a refusal that names what cannot
    be done and what to do instead, rather than a fixture that quietly lies.

    Checked against the OVERLAY's own keys (``seed_overlay_collections``,
    laid on by the profile loader) rather than by comparing seeds, so it
    holds however the overlay arrived -- the ``seed_overlay=`` parameter, a
    ``VENDORFAKE_SEED_OVERLAY`` entry in ``env=``, or a path either of them
    named.
    """
    named = config.seed_overlay_collections
    if not named:
        return
    bound = seed_collections_for(vendor, definition=definition)
    offending = sorted(set(named) & bound)
    if not offending:
        return
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=(
            f"seed overlay names {', '.join(repr(name) for name in offending)}, which is what "
            f"{vendor}'s .seed is built from. The seed handed back by unit(), async_unit() and served() "
            f"describes the SHIPPED credentials and identity -- its bearer tokens and its tenant id come "
            f"from this distribution's own constants, not from the seed document that was loaded -- so it "
            f"cannot follow an overlay. A unit built on this one would hydrate the overlaid scenario while "
            f".seed.auth still carried the shipped bearer, and every request made with it would answer 401 "
            f"with nothing anywhere to say why. Overlay any other collection the seed document carries. To "
            f"change the credentials or the identity themselves, run the unit on a profile whose own seed "
            f"document carries the ones you want (its `seed` key, or VENDORFAKE_SEED) and read them from "
            f"that document rather than from .seed."
        ),
        field="seed_overlay",
        info={"seed_bound": offending, "named": list(named), "vendor": vendor, "profile": profile},
    )


@overload
def unit(
    vendor: Literal["square"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: SquareSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractContextManager[StartedUnit[SquareSeed]]: ...


@overload
def unit(
    vendor: Literal["clover"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: CloverSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractContextManager[StartedUnit[CloverSeed]]: ...


@overload
def unit(
    vendor: Literal["toast"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: ToastSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractContextManager[StartedUnit[ToastSeed]]: ...


@overload
def unit(
    vendor: str,
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractContextManager[StartedUnit[Seed]]: ...


def unit(
    vendor: str,
    profile: str | None = None,
    *,
    capabilities: Sequence[str] | None = None,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
    seed: int | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
    unmatched: UnmatchedPolicy | None = None,
    clock_start: datetime | str | None = None,
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

    ``profile`` defaults to ``None``, which resolves via
    :func:`~vendorfake.registry.create_unit` / ``load_profile`` in the same
    three steps ``vendorfake serve`` uses: an explicit ``profile=`` argument
    wins; failing that, ``VENDORFAKE_PROFILE`` in the ``env=`` mapping given
    to *this call*; failing that, ``full``. Prior to this release ``unit()``
    passed the literal string ``"full"`` to ``create_unit``, so an explicit
    default always beat the environment and a caller's ``env=`` mapping could
    never change which profile an in-process unit started on -- **this is a
    behaviour change**, recorded in ``CHANGELOG.md``: a caller who builds one
    ``env`` mapping for a whole test module and passes it to both
    :func:`served` (a real environment) and ``unit()`` (which never read
    ``VENDORFAKE_PROFILE`` from it before) will now see ``unit()`` start on
    that profile too. See :func:`~vendorfake.registry.create_unit` for
    exactly how a capability request resolves to a profile instead when
    ``capabilities`` is given; supplying both ``profile`` and ``capabilities``
    is a ``ValueError``, and so is an empty ``capabilities=[]``.

    ``seed_overlay`` is a partial seed document merged over the profile's
    before the store is built -- an inline mapping, or a path to a JSON file.
    On a vendor named as a literal it is typed: ``unit("square",
    seed_overlay={"orders": [...]})`` type-checks and a key that is not one
    of Square's seed collections does not. ``tokens`` and the vendor's
    identity collection are refused at start: ``.seed`` carries the shipped
    credentials and tenant id and cannot follow an overlay. See :func:`_unit`
    for the layering and ``docs/concepts/seed.md`` for the merge rule.
    """
    return _unit(
        vendor,
        profile,
        capabilities=capabilities,
        sink=sink,
        env=env,
        logger=logger,
        seed=seed,
        seed_overlay=seed_overlay,
        unmatched=checked_unmatched(unmatched),
        clock_start=clock_start,
    )


@contextmanager
def _unit(
    vendor: str,
    profile: str | None = None,
    *,
    capabilities: Sequence[str] | None = None,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
    seed: int | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
    unmatched: UnmatchedPolicy | None = None,
    clock_start: datetime | str | None = None,
) -> Iterator[StartedUnit[Seed]]:
    """The body of :func:`unit`. See that function for the contract.

    ``sink`` defaults to real HTTP delivery, so a subscribed
    :func:`webhook_receiver` sees signed bytes; pass
    ``vendorfake.core.webhooks.sink.MemorySink()`` to capture in memory
    instead. ``env`` is the ``VENDORFAKE_*`` layer, empty by default -- the
    process environment is never read here, so one test's variables cannot
    change another test's profile.

    **Asymmetric with** :func:`served` in two ways. ``served()``'s ``env=``
    is a layer *over* the child's inherited ``os.environ`` rather than the
    whole environment, because a child process has to be reachable from the
    shell that spawned it. And ``served()`` keeps a plain ``profile: str =
    "full"`` and has no ``capabilities=`` parameter, so a ``VENDORFAKE_PROFILE``
    entry in a shared ``env`` mapping influences ``unit()`` and never
    ``served()``, and a capability request cannot be moved from one to the
    other without first resolving it to a profile name by hand.

    **Ids are deterministic per unit.** Two units on the same profile mint the
    same order ids, tokens and codes in the same order, because each starts
    its id stream from the profile's seed -- that is what makes an id
    assertion stable across runs. Their stores are separate, so nothing
    collides; but a test that compares ids *between* two units, or keeps
    ids across a unit's lifetime as if they were unique, will be surprised.
    ``seed`` restarts the stream (and the fault engine's RNG) from another
    number, for the rare test that needs two units to diverge; it is the
    ``VENDORFAKE_CHAOS_SEED`` layer, so an explicit ``env`` entry wins.

    ``seed_overlay`` is a **partial seed document** laid over the one the
    profile names -- a mapping (the document itself) or a ``str`` /
    ``os.PathLike`` naming a JSON file. It is merged before the store is
    hydrated, so the unit answers from the merged scenario from its first
    request; the merge rule is stated once, in ``docs/concepts/seed.md``, and
    implemented once, in
    :func:`~vendorfake.core.config.overlay.merge_seed`. Like ``seed`` and
    ``clock_start`` it is a layer *under* ``env``: it becomes the
    ``VENDORFAKE_SEED_OVERLAY`` entry, so an explicit entry in a shared
    ``env`` mapping wins, and the base it merges onto is whatever seed
    document actually loaded -- the profile's, or the one a
    ``VENDORFAKE_SEED`` entry pointed at instead. A top-level key that is not
    one of the seed's collections is refused when the unit starts, naming the
    key and the collections that exist; on a vendor named as a literal the
    per-vendor ``TypedDict`` (``SquareSeedOverlay`` and its siblings) makes a
    type checker say so first.

    Two collections are refused as well, though they are real: ``tokens`` and
    the vendor's identity collection (``merchant``, ``restaurant``). ``seed``
    below carries the shipped credentials and tenant id from this
    distribution's own constants rather than from the document that loaded, so
    an overlay of those two would hydrate one scenario while ``.seed``
    described another and every request made with ``.seed.auth`` answered 401.
    To run on other credentials, point the profile at a whole seed document of
    your own instead.

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

    ``clock_start`` (a timezone-aware ``datetime``, or an RFC 3339 string) is
    ``VENDORFAKE_CLOCK_START``: the instant a virtual clock starts at, so two
    units built from it agree on every expiry down to the second -- see
    :meth:`Driver.clock`. It requires ``clock.mode="virtual"`` and raises
    rather than switching modes for you: set ``env={"VENDORFAKE_CLOCK":
    "virtual"}`` (or ``VENDORFAKE_CLOCK=virtual`` for :func:`served`)
    yourself, so a test that forgot it fails loudly instead of silently
    running on a real clock the pinned start never touches.
    """
    environ: dict[str, str] = {} if seed is None else {"VENDORFAKE_CHAOS_SEED": str(seed)}
    if clock_start is not None:
        environ["VENDORFAKE_CLOCK_START"] = _clock_start_env_value(clock_start)
    if seed_overlay is not None:
        environ["VENDORFAKE_SEED_OVERLAY"] = _seed_overlay_env_value(seed_overlay)
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
    transport = UnitTransport(built, unmatched=unmatched)
    started: StartedUnit[Any] | None = None
    try:
        # Before the client, so a vendor with no seed is refused with the unit
        # already stopped by the `finally` rather than left running behind a
        # half-built driver. Named with `built.name` and the config's own
        # `profile`, not the raw arguments above: `seed_for` is keyed on the
        # resolved vendor, and a registry alias or a profile default (from
        # `env`) can make the resolved values differ from what the caller
        # spelled, in which case the refusal should name what was actually
        # looked up.
        #
        # `definition=built.context.vendor` -- the exact `VendorDefinition`
        # instance `create_unit` built this unit from, off the running unit
        # itself -- not omitted the way this call used to leave it. Omitting
        # it sends `seed_for` to `registry.resolve_vendor(built.name)` for a
        # SECOND, independent lookup, which is only ever safe if that second
        # call is guaranteed to return the same object the unit is running
        # on. It is not: `square/__init__.py`'s own module docstring states
        # the opposite invariant in capitals -- a fresh `VendorDefinition` on
        # every access, because a vendor owns a stateful, seeded id stream
        # and two units (or, here, one unit and one orphaned lookup) sharing
        # one would interleave their draws. A third-party `SeedingVendor`
        # built the same way calls its hook on a definition that never saw
        # this unit's requests, and hands back credentials or ids from a
        # stream this unit's store never touched -- silently, since nothing
        # here raises or warns; `started.seed` just carries the wrong
        # instance's identity. `_served` already passes `definition=` for
        # this exact reason (see its own call below); this is the primary
        # binding, and the one the `SeedingVendor` hook exists for, so
        # leaving it unfixed there was the more consequential half of the
        # gap. `built.context.vendor` costs nothing extra: it is a `Unit`
        # attribute, not a registry call.
        #
        # Before the seed is built, because the refusal is *about* the seed
        # that would otherwise be built: an overlay naming the collection
        # `.seed` carries the shipped values of makes the seed below describe
        # a unit other than this one. The unit is already constructed by the
        # time this runs -- the overlay's keys are known only once the profile
        # loader has read it -- but nothing has been requested of it, and the
        # `finally` above stops it, so no caller ever sees the unit at all.
        _refuse_a_seed_bound_overlay(
            built.name, built.context.config.profile, built.context.config, built.context.vendor
        )
        resolved_seed = _require_seed(
            built.name,
            built.context.config.profile,
            seed_for(built.name, built.context.config.vendor_config, definition=built.context.vendor),
        )
        with httpx.Client(transport=transport, base_url=IN_PROCESS_BASE_URL, timeout=CLIENT_TIMEOUT_S) as client:
            started = StartedUnit(
                vendor=built.name,
                profile=built.context.config.profile,
                base_url=IN_PROCESS_BASE_URL,
                client=client,
                seed=resolved_seed,
                unit=built,
                tripwire=tripwire,
                _transport=transport,
            )
            yield started
    finally:
        if started is not None:
            _release_async_client(started)
        built.stop()


def _release_async_client(started: StartedUnit[Any]) -> None:
    """Close a lazily built :attr:`StartedUnit.async_client` from sync code.

    ``AsyncClient.aclose`` is a coroutine, and :func:`unit` is a synchronous
    context manager, so there are two cases and they are not symmetric.

    **No loop running.** ``asyncio.run`` finishes the close, which for this
    client is one state flag: ``aclose`` awaits the transport's ``aclose``, and
    :class:`UnitTransport` inherits the no-op. Cheap and exact.

    **A loop already running** -- an ``async def`` test that used the plain
    ``with unit(...)`` block, which is the shape the README shows. Nothing is
    done, deliberately. A synchronous ``__exit__`` cannot await inside a live
    loop, and the two ways round it are both worse than doing nothing:
    ``asyncio.run`` raises there, and scheduling a task from an exiting fixture
    leaves a coroutine that may be garbage-collected before the loop runs it,
    which is a warning rather than a close. Nothing leaks either way -- this
    transport owns no socket, no pool and no thread; ``aclose`` only marks the
    client closed. Use :func:`async_unit` where the close should be real, and
    it is awaited.
    """
    client = started._async_client
    if client is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(client.aclose())


@overload
def async_unit(
    vendor: Literal["square"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: SquareSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractAsyncContextManager[StartedUnit[SquareSeed]]: ...


@overload
def async_unit(
    vendor: Literal["clover"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: CloverSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractAsyncContextManager[StartedUnit[CloverSeed]]: ...


@overload
def async_unit(
    vendor: Literal["toast"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: ToastSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractAsyncContextManager[StartedUnit[ToastSeed]]: ...


@overload
def async_unit(
    vendor: str,
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractAsyncContextManager[StartedUnit[Seed]]: ...


def async_unit(
    vendor: str,
    profile: str | None = None,
    *,
    capabilities: Sequence[str] | None = None,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
    seed: int | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
    unmatched: UnmatchedPolicy | None = None,
    clock_start: datetime | str | None = None,
) -> AbstractAsyncContextManager[StartedUnit[Any]]:
    """:func:`unit`, for a consumer whose fixtures are ``async def``.

    Yields the same :class:`StartedUnit`, with the same arguments and the same
    meaning for every one of them: this delegates to :func:`unit` rather than
    repeating its construction, so there is one code path and two entry points.
    A second copy would be a second place for the environment layering, the
    seed and the tripwire wiring to drift, and the drift would be silent.

    The overloads mirror :func:`unit`'s, so ``async_unit("clover")`` yields a
    ``StartedUnit[CloverSeed]`` to a checker for the same reason. There is one
    implementation, in :func:`_async_unit`; the overloads are declarations,
    for the reason :func:`unit` gives.

    What it adds is the exit: :meth:`StartedUnit.aclose` is awaited, so the
    async client is genuinely closed rather than left for the loop.

    Both clients are available inside the block -- ``async_client`` and the
    synchronous ``client`` -- because a test often needs a synchronous set-up
    call before the code under test runs.
    """
    return _async_unit(
        vendor,
        profile,
        capabilities=capabilities,
        sink=sink,
        env=env,
        logger=logger,
        seed=seed,
        seed_overlay=seed_overlay,
        # Checked here, at the call, and not only inside ``unit()`` on
        # ``__aenter__``: the refusal should land on the line that spelled
        # the value, before an ``async with`` is entered.
        unmatched=checked_unmatched(unmatched),
        clock_start=clock_start,
    )


@asynccontextmanager
async def _async_unit(
    vendor: str,
    profile: str | None = None,
    *,
    capabilities: Sequence[str] | None = None,
    sink: DeliverySink | None = None,
    env: Mapping[str, str] | None = None,
    logger: Logger | None = None,
    seed: int | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
    unmatched: UnmatchedPolicy | None = None,
    clock_start: datetime | str | None = None,
) -> AsyncIterator[StartedUnit[Seed]]:
    """The body of :func:`async_unit`. See that function for the contract."""
    with unit(
        vendor,
        profile,
        capabilities=capabilities,
        sink=sink,
        env=env,
        logger=logger,
        seed=seed,
        seed_overlay=seed_overlay,
        unmatched=unmatched,
        clock_start=clock_start,
    ) as started:
        try:
            yield started
        finally:
            await started.aclose()


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
    env: Mapping[str, str] | None = ...,
    clock_start: datetime | str | None = ...,
    seed_overlay: SquareSeedOverlay | str | os.PathLike[str] | None = ...,
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
    env: Mapping[str, str] | None = ...,
    clock_start: datetime | str | None = ...,
    seed_overlay: CloverSeedOverlay | str | os.PathLike[str] | None = ...,
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
    env: Mapping[str, str] | None = ...,
    clock_start: datetime | str | None = ...,
    seed_overlay: ToastSeedOverlay | str | os.PathLike[str] | None = ...,
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
    env: Mapping[str, str] | None = ...,
    clock_start: datetime | str | None = ...,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = ...,
) -> AbstractContextManager[ServedUnit[Seed]]: ...


def served(
    vendor: str,
    profile: str = "full",
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    log_level: str = "error",
    timeout_s: float = STARTUP_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
    clock_start: datetime | str | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
) -> AbstractContextManager[ServedUnit[Any]]:
    """``vendorfake serve`` in a child process, with its URL.

    Overloaded on the vendor literal for the same reason :func:`unit` is: the
    child serves the vendor it was told to, so the seed's type is knowable at
    the call site and there is no reason to make a consumer prove it with an
    ``isinstance``. Delegates to a private generator; see :func:`unit`.

    ``env`` is the ``VENDORFAKE_*`` layer for this one child, on top of the
    environment it inherits -- the served counterpart of :func:`unit`'s
    ``env=``, so two differently-seeded children can run in one process
    without either touching ``os.environ``. ``clock_start`` layers the same
    way. See :func:`_served` for the precedence and the one variable the
    mapping cannot reach.

    ``seed_overlay`` is :func:`unit`'s partial seed document, reaching the
    child as ``VENDORFAKE_SEED_OVERLAY`` -- a mapping is encoded as canonical
    JSON, a ``str`` or ``os.PathLike`` is passed as the path and read by the
    child relative to the working directory both processes share. Narrowed on
    the vendor literal the same way. An ``env=`` entry naming that variable is
    refused: this is the parameter for it, and only the parameter's path
    checks the overlay in *this* process, where the refusal is visible. An
    overlay naming ``tokens`` or the vendor's identity collection is refused
    here too, before the child is spawned -- ``.seed`` is built in this
    process from the vendor's constants, so a child hydrated from an overlaid
    credential would answer 401 to every request made with ``.seed.auth``.

    **Sharing one child across tests:** a session-scoped ``served()`` against
    a vendor with single-use or rotating state (Clover's refresh rotation)
    needs :meth:`Driver.reset` between tests, or the second test to consume
    that state fails for a reason unrelated to what it tests -- see
    ``docs/concepts/chaos-rules-and-faults.md``, "Sharing one unit across
    tests".
    """
    return _served(
        vendor,
        profile,
        port=port,
        host=host,
        log_level=log_level,
        timeout_s=timeout_s,
        env=env,
        clock_start=clock_start,
        seed_overlay=seed_overlay,
    )


@contextmanager
def _served(
    vendor: str,
    profile: str = "full",
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    log_level: str = "error",
    timeout_s: float = STARTUP_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
    clock_start: datetime | str | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
) -> Iterator[ServedUnit[Seed]]:
    """The body of :func:`served`. See that function for the contract.

    Runs the interpreter this test runs under (``python -m vendorfake``), so
    whatever environment installed ``vendorfake`` serves it. ``port=0`` lets
    the operating system choose, and the CLI announces the number before it
    accepts a request. The child is asked to stop with ``SIGTERM`` -- uvicorn's
    graceful path -- and killed only if it ignores that.

    ``env`` is layered onto this process's environment for the child --
    inherited ``os.environ`` first, then the mapping, entry by entry -- so an
    entry in it beats an ambient variable of the same name and nothing in
    ``os.environ`` is written. That is the whole difference from
    :func:`unit`, whose ``env=`` is the *entire* layer over an empty mapping:
    a served child stands in for a vendor and has to be reachable from
    whatever ``PATH``, locale and proxy settings the shell provides, so
    inheriting is the only sane default and the mapping is a delta on it.
    ``VENDORFAKE_CLOCK=virtual``, ``VENDORFAKE_CAPABILITIES`` (an absolute
    list or a delta on the profile's), a ``VENDORFAKE_VENDOR_*`` credential
    override, the webhook and request-log variables: all reach the child
    through here. Eight are refused with ``ValueError`` before the child is
    spawned, rather than silently beaten. ``VENDORFAKE_PROFILE``,
    ``VENDORFAKE_HOST``, ``VENDORFAKE_PORT`` and ``VENDORFAKE_LOG_LEVEL`` are
    the four things this function passes to the child as explicit flags
    (``profile=``, ``host=``, ``port=``, ``log_level=``), and the CLI prefers
    a flag to the variable, so the entry would change nothing -- use the
    parameter. ``VENDORFAKE_TRANSPORT`` and ``VENDORFAKE_TRANSPORT_DIR`` are
    ignored by ``serve``, which only ever binds HTTP. ``VENDORFAKE_SEED`` is refused because the seed handed
    back on ``.seed`` is derived from the vendor's module constants, not read
    from a document, and could not follow an alternate one -- the child would
    answer with tokens the seed does not carry.
    ``VENDORFAKE_SEED_OVERLAY`` is refused because ``seed_overlay=`` is the
    parameter for it: the parameter takes the document as a mapping, encodes
    it for the child, and loads it in *this* process too, so an overlay naming
    a collection the vendor does not have raises here rather than killing a
    child before it announces a port. There is still no
    ``capabilities=`` parameter;
    resolve a capability request to a profile name by hand (or via
    :func:`unit`) before passing it as ``profile=``.

    Both pipes are read on a daemon thread for the life of the child, so a
    child that logs more than the pipe buffers cannot block mid-test, and
    ``timeout_s`` is a real deadline: a child that never announces -- wedged,
    or not vendorfake at all -- is stopped and reported rather than waited
    on forever.

    ``clock_start`` is :func:`unit`'s ``VENDORFAKE_CLOCK_START`` control,
    layered the same way ``env`` is and *below* it -- an explicit
    ``VENDORFAKE_CLOCK_START`` entry in ``env`` wins, exactly as it does in
    :func:`unit`, so one mapping built for a whole module means the same
    thing to both. It requires ``VENDORFAKE_CLOCK=virtual``, from ``env`` or
    from the shell; the child raises the same loud refusal :func:`unit` does
    rather than switching modes for you, and a refusal here surfaces as the
    child exiting before it announces a port.

    One family of those variables does not stay purely the child's concern:
    every ``VENDORFAKE_VENDOR_*`` entry the child will see -- from
    ``os.environ`` or from ``env``, with the same precedence -- is read here
    too, and layered onto the profile's own ``vendor`` block the same way
    ``resolve_config`` layers it for the child, so the seed handed back
    agrees with the credentials the served unit actually answers with. Every
    other variable is the child's alone.

    A nonexistent (or otherwise malformed) ``profile`` is refused with
    ``UnitError`` -- the same exception :func:`unit` raises for the identical
    mistake -- before ``subprocess.Popen`` ever runs, because the profile is
    now loaded in this process to resolve the seed hook's ``vendor_config``
    before the seed check below. Before that, a bad profile name reached
    ``served()`` only as whatever failure the spawned child's own
    startup/health-check path produced; a caller relying on that older,
    slower failure mode -- catching a connection or startup-timeout error
    around the ``with served(...)`` block -- now sees an unhandled
    ``UnitError`` instead.
    """
    # Resolved and refused before the child is spawned: `registry.resolve_vendor`
    # is the same registry lookup `create_unit` pays for internally, and
    # `load_profile` below is the same profile loader `create_unit` calls too
    # -- neither needs a running unit. Paying for a subprocess that boots,
    # announces its port and answers a health check only to be told the
    # vendor has no seed (or was seeded from the wrong vendor block) wastes
    # the startup on every call in a suite that does this per test, and
    # points the traceback at a line inside a connected client rather than at
    # the vendor argument that is actually wrong.
    #
    # `env=vendor_env` -- not the no-`env=` this call used before -- because
    # the child below is *not* built from `profile` alone: it inherits this
    # process's whole `os.environ` (see the `Popen` call further down), and
    # `cli.py`'s own `_serve` layers every `VENDORFAKE_VENDOR_*` variable in
    # it onto the profile's `vendor` block before building its unit. Loading
    # the profile here with no `env=` (the previous shape of this call)
    # computed a `vendor_config` that quietly stopped matching the child's the
    # moment such a variable was set -- review found this: a suite exporting
    # `VENDORFAKE_VENDOR_APPLICATION_ID` for the whole run got a seed here
    # that still carried the profile document's own id, while the served
    # unit answered with the overridden one. `vendor_env` is filtered to just
    # that one prefix rather than passed as the whole of `os.environ`,
    # because `resolve_config` also reads `VENDORFAKE_CAPABILITIES`,
    # `VENDORFAKE_CLOCK*` and the webhook-URL variables from `env=`, and
    # nothing here needs them: only `loaded.config.vendor_config` is read
    # below, and pulling in the rest would let an unrelated ambient variable
    # (a stray `VENDORFAKE_CLOCK_START` without `VENDORFAKE_CLOCK=virtual` in
    # the caller's shell, say) fail this call for a reason that has nothing
    # to do with the seed it computes -- a failure `served()`'s own
    # `clock_start=` handling below is deliberately the only thing that
    # should be able to trigger, and did before this change.
    #
    # This is a narrow, deliberate second exception to `cli.py`'s "the only
    # module that reads `os.environ`" invariant (see that module's docstring)
    # -- the same exception the child-spawning `env=` handling further down
    # already is, for the same reason: `served()` hands a subprocess the real
    # environment by construction (that is what "serve this in a child
    # process" means), so the parent-side computation that has to agree with
    # what that child resolves cannot be built from an empty mapping the way
    # `unit()`'s can. Reading a name *to pass to the child unchanged* and
    # reading the one prefix of it *this process also needs to agree with*
    # are the same underlying fact about `served()`, not two different
    # invariant violations.
    #
    # `seed_for` is handed the profile's real `vendor` block --
    # `loaded.config.vendor_config` -- exactly as `unit()` hands it
    # `built.context.config.vendor_config`. Review round 1 caught this
    # passing `{}` instead: invisible for the three built-in vendors, whose
    # config models default to exactly what the shipped profiles carry, and
    # wrong for a third-party `SeedingVendor` hook the moment its profile
    # overrides anything. `definition=` is passed too, which is what lets
    # this cost only the one registry lookup already paid for above, rather
    # than a second one inside `seed_for`.
    #
    # Called as `registry.resolve_vendor` -- an attribute lookup on the
    # module -- rather than through a name bound at import time: `unit()`
    # resolves the vendor by calling `create_unit`, which looks up
    # `resolve_vendor` in `registry.py`'s own namespace, so a test that
    # substitutes `vendorfake.registry.resolve_vendor` (patching the
    # attribute) reaches it. A bare `from vendorfake.registry import
    # resolve_vendor` here would bind a second, separate name in this
    # module's namespace that the same substitution does not touch --
    # `served()` would keep calling the original function while every caller
    # believed the vendor had been substituted. The module reference is what
    # keeps `unit()` and `served()` resolving through the one indirection a
    # test can patch.
    definition = registry.resolve_vendor(vendor)
    resolved_name = definition.name
    # The child's layer, in the order the child will see it: the convenience
    # `clock_start` first, then the caller's mapping, so an explicit entry in
    # `env` wins over the kwarg -- `_unit` applies its `env` last for the same
    # reason, and the two must agree on what one shared mapping means.
    layer: dict[str, str] = {}
    if clock_start is not None:
        layer["VENDORFAKE_CLOCK_START"] = _clock_start_env_value(clock_start)
    layer.update(env or {})
    # Refused here, before the child exists, rather than discovered as a 401
    # three assertions later: the parent-side `resolved_seed` below comes from
    # `seed_for`, which derives every id and token from the vendor's module
    # constants and never reads the document `VENDORFAKE_SEED` points at, so
    # the child would hydrate from one scenario while `.seed` described
    # another. Review caught this with a measured 401 on `.seed.auth` against
    # a child seeded from an alternate file.
    if "VENDORFAKE_SEED" in layer:
        raise ValueError(
            "served(env=...) cannot carry VENDORFAKE_SEED: the .seed handed back is derived from the vendor's "
            "constants, not from a seed document, and would not describe the child. Use a profile whose "
            "document points at the seed you want, and pass it as profile=."
        )
    # The same family of refusal, one variable along: `seed_overlay=` is the
    # parameter, and it is layered below `env` only in the sense that nothing
    # in `env` may name it at all. An entry here would reach the child
    # unvalidated by this process -- the mapping is passed through verbatim --
    # so a misspelled collection would surface as the child exiting before it
    # announced a port, with the real refusal buried in its stderr, rather
    # than as the `UnitError` the parameter's own path raises where the caller
    # can see it. The parameter also accepts a mapping; the variable cannot.
    if "VENDORFAKE_SEED_OVERLAY" in layer:
        raise ValueError(
            "served(env=...) cannot carry VENDORFAKE_SEED_OVERLAY: pass seed_overlay= instead. The parameter "
            "takes the document itself as a mapping (or a path), encodes it for the child, and refuses an "
            "unknown collection where the caller can see it -- an env entry would reach the child unchecked "
            "and surface as a child that exited before announcing a port."
        )
    # The same shape of refusal for the variables an explicit flag below would
    # silently beat (konyklabs/roadmap#105): a mapping entry that changes
    # nothing is worse than one that is refused, because the caller reads
    # "env reaches the child" and then debugs a child still logging at
    # `error`, still on a random port, still on loopback.
    transport_keys = sorted({"VENDORFAKE_TRANSPORT", "VENDORFAKE_TRANSPORT_DIR"} & set(layer))
    if transport_keys:
        raise ValueError(
            f"served(env=...) cannot carry {', '.join(transport_keys)}: `vendorfake serve` only ever binds HTTP, "
            "so the entry would change nothing, and there is no parameter to use instead -- a served unit is an "
            "HTTP unit by definition. Build a unit in-process for any other binding."
        )
    beaten = sorted(_FLAG_BEATEN_ENV & set(layer))
    if beaten:
        raise ValueError(
            f"served(env=...) cannot carry {', '.join(beaten)}: served() passes {_FLAG_BEATEN_HINT} to the child "
            "as explicit flags, and the CLI prefers a flag to the variable, so the entry would change nothing. "
            "Use the parameter instead."
        )
    vendor_env = {key: value for key, value in {**os.environ, **layer}.items() if key.startswith(ENV_VENDOR_PREFIX)}
    if seed_overlay is not None:
        # Encoded once and used twice: the child reads it from its environment
        # (below), and THIS process reads it too, through the `load_profile`
        # call that follows. That second read is the eager refusal `served()`
        # already promises for a bad profile and a seedless vendor -- an
        # overlay naming a collection the vendor does not have raises the same
        # `UnitError` `unit()` raises, here, before `Popen`, instead of
        # surfacing as a child that exited before announcing a port with the
        # real message buried in its stderr. It is added to `vendor_env` --
        # otherwise filtered to `VENDORFAKE_VENDOR_*` for the reason above --
        # because it comes from this call's own parameter and not from the
        # ambient environment, so it cannot import an unrelated shell variable
        # into a computation that is meant to be about the seed alone.
        overlay_value = _seed_overlay_env_value(seed_overlay)
        vendor_env["VENDORFAKE_SEED_OVERLAY"] = overlay_value
        layer["VENDORFAKE_SEED_OVERLAY"] = overlay_value
    loaded = load_profile(
        profile_dir=definition.profile_dir,
        name=profile,
        base_dir=definition.base_dir,
        env=vendor_env,
        defaults=definition.retry_defaults,
    )
    # Before `Popen`, like every other refusal `served()` makes: the child
    # would hydrate the overlaid credentials while the `resolved_seed` built
    # here still carried the shipped ones, and the caller would meet that as a
    # 401 from a child that started perfectly. Same helper as `_unit`'s, on
    # the config this process loaded with the child's own overlay in it.
    _refuse_a_seed_bound_overlay(resolved_name, profile, loaded.config, definition)
    resolved_seed = _require_seed(
        resolved_name, profile, seed_for(resolved_name, loaded.config.vendor_config, definition=definition)
    )
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
    # `cli.py` is documented as the only module that reads `os.environ` to
    # resolve a unit *built in that process*, so that a stray shell variable
    # cannot silently change which profile a unit in *this* process resolves
    # to -- `vendor_env` above is `served()`'s one narrow, documented
    # exception to that, and this is the other: reading it here is for a
    # different reason with the opposite failure mode. `Popen(argv)` with no
    # `env=` already inherits the whole of `os.environ` for the child
    # implicitly (that is plain subprocess behaviour, unrelated to this
    # project), and `Popen`'s `env=` replaces rather than layers -- so naming
    # one more variable for the child without dropping the rest of its
    # inherited environment has no path that avoids this dict read. `None`
    # (the exact prior behaviour) is used whenever there is nothing to layer:
    # neither `env` nor `clock_start` was given.
    child_env = None if not layer else {**os.environ, **layer}
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env)
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
                # `resolved_seed` is built above, before the child exists, from
                # the same profile document and the same `VENDORFAKE_VENDOR_*`
                # environment layer the child resolves its own config from
                # (`vendor_env`, above) -- not the child's profile read back
                # over the wire, which no route publishes. A custom profile's
                # overrides, and an ambient `VENDORFAKE_VENDOR_*` override,
                # both reach this seed the same way they reach the served
                # unit's real credentials.
                seed=resolved_seed,
                process=process,
                _output=output,
            )
    finally:
        _stop(process)
        output.join()


_FLAG_BEATEN_ENV: frozenset[str] = frozenset(
    {"VENDORFAKE_PROFILE", "VENDORFAKE_HOST", "VENDORFAKE_PORT", "VENDORFAKE_LOG_LEVEL"}
)
"""The ``VENDORFAKE_*`` names an ``env=`` entry to :func:`served` is refused
for because the child gets each as a flag (``--profile``, ``--host``,
``--port``, ``--log-level``) that beats the variable; the refusal names the
parameter to use. ``VENDORFAKE_TRANSPORT``, ``VENDORFAKE_TRANSPORT_DIR``,
``VENDORFAKE_SEED`` and ``VENDORFAKE_SEED_OVERLAY`` are refused too, each with
its own reason -- and of those four only the overlay has a parameter to use
instead (``seed_overlay=``). See :func:`_served`."""

_FLAG_BEATEN_HINT = "profile=, host=, port= and log_level="

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
