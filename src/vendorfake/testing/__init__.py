"""The fixtures a consumer's test suite reaches for. :func:`unit` builds one in
this process behind an ``httpx.Client``, :func:`async_unit` is the same as an
``async with``, :func:`served` runs ``vendorfake serve`` in a child process, and
:func:`serve_in_thread` puts a real server on a background thread. Each yields a
:class:`Driver` whose methods wrap the control plane."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
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
from typing import Any, Generic, Literal, TypeVar, overload

import httpx

from vendorfake import registry
from vendorfake.core.config.models import ResolvedConfig, UnmatchedPolicy
from vendorfake.core.config.profile import DEFAULT_PROFILE_NAME, ENV_SEED, ENV_VENDOR_PREFIX, load_profile
from vendorfake.core.control.plane import DEFAULT_REQUEST_LIMIT
from vendorfake.core.kernel.nearmiss import NEAR_MISS_HEADER
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
    LightspeedSeed,
    LightspeedSeedOverlay,
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
from vendorfake.testing.transport import DEFAULT_INPROCESS_POLICY, UnitTransport, UnmatchedRequest, checked_unmatched

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
    "LightspeedSeed",
    "LightspeedSeedOverlay",
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
"""Which vendor's seed a driver carries; a plain ``str`` vendor binds
:class:`~vendorfake.testing.seeds.Seed`. Covariant, because the literal overloads
overlap the ``str`` fallback, so reassigning ``.seed`` is a consumer's hazard."""

IN_PROCESS_BASE_URL = "http://vendorfake.local"
"""The host an in-process client addresses, never resolved: it exists only so a
relative path has something to be relative to."""

STARTUP_TIMEOUT_S = 60.0
SHUTDOWN_TIMEOUT_S = 15.0

CLIENT_TIMEOUT_S = 30.0
"""The HTTP timeout for every client this module builds. One constant, so none
drift onto httpx's 5s default, shorter than a real-clock :meth:`Driver.drain`."""

DRAIN_TIMEOUT_S = 120.0
"""How long :meth:`Driver.drain` waits, overriding the client timeout for that
one call; the shipped profiles' longest cascade settles in about fifteen."""
_LISTENING = re.compile(r"listening on http://([^:\s]+):(\d+)")


@dataclass(frozen=True, slots=True)
class ClockInfo:
    """The unit's clock, as :meth:`Driver.clock` reads it off ``/__unit/info``."""

    now: datetime
    mode: Literal["real", "virtual"]


def _clock_start_env_value(clock_start: datetime | str) -> str:
    """``VENDORFAKE_CLOCK_START``'s value, from either spelling accepted. A naive
    ``datetime`` raises: it would pin a different instant on each machine."""
    if isinstance(clock_start, str):
        return clock_start
    if clock_start.tzinfo is None:
        raise ValueError(
            f"clock_start={clock_start!r} has no timezone. A naive datetime has no defined instant across "
            "machines; pass a timezone-aware one (e.g. datetime(..., tzinfo=UTC)) or an RFC 3339 string."
        )
    return clock_start.isoformat()


def _seed_overlay_env_value(seed_overlay: Mapping[str, Any] | str | os.PathLike[str]) -> str:
    """``VENDORFAKE_SEED_OVERLAY``'s value, from either spelling accepted. A
    mapping becomes the document as canonical JSON, so key order does not change
    its digest; a path goes through ``os.fspath``, and the loader reads a value
    starting with ``{`` as inline JSON and the rest as a path."""
    if isinstance(seed_overlay, Mapping):
        return canonical_json({str(key): value for key, value in seed_overlay.items()})
    return os.fspath(seed_overlay)


@dataclass
class Driver(Generic[SeedT]):
    """A unit you can talk to, however it was started, generic in its seed so
    ``.seed`` needs no ``isinstance``. A vendor with no seed is refused where the
    unit is built rather than handed back as an ``Optional``."""

    vendor: str
    profile: str
    base_url: str
    client: httpx.Client
    seed: SeedT
    _async_client: httpx.AsyncClient | None = field(default=None, kw_only=True, repr=False)

    @property
    def async_client(self) -> httpx.AsyncClient:
        """An ``httpx.AsyncClient`` onto the same unit over the same base URL, built
        lazily so it binds the loop that first uses it; closed with the driver."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self.base_url, timeout=CLIENT_TIMEOUT_S, event_hooks=_async_hooks(self.client)
            )
        return self._async_client

    async def aclose(self) -> None:
        """Close the async client if one was built; safe to call twice."""
        if self._async_client is not None:
            await self._async_client.aclose()

    # -- reading ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/health"))

    def info(self) -> dict[str, Any]:
        return self._json(self.client.get("/__unit/info"))

    def clock(self) -> ClockInfo:
        """The unit's clock right now: its mode and its instant. Reads
        ``/__unit/info`` and advances nothing, real or virtual."""
        payload = self.info()["clock"]
        return ClockInfo(now=datetime.fromisoformat(str(payload["now"])), mode=payload["mode"])

    def _route_table(self) -> list[dict[str, Any]]:
        return list(self._json(self.client.get("/__unit/routes"))["routes"])

    def route_for(self, operation_id: str) -> RouteInfo:
        """The route named ``operation_id``, from ``GET /__unit/routes`` so it works
        over any binding. ``KeyError`` names every id this unit registers."""
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
        """``self.route_for(operation_id).path``, for a caller wanting only it."""
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
        """What the code under test called, newest first, reads and 4xx included.
        ``operation_id`` is the filter to prefer, ``route`` matches
        ``"POST /v2/orders"``, and ``unmatched=True`` narrows to calls no route
        answered. Control-plane traffic, bodies and headers are never recorded."""
        query: dict[str, str] = {"limit": str(limit)}
        if operation_id is not None:
            query["operation_id"] = operation_id
        if route is not None:
            query["route"] = route
        if unmatched is not None:
            query["unmatched"] = "true" if unmatched else "false"
        return list(self._json(self.client.get("/__unit/requests", params=query))["requests"])

    def clear_requests(self) -> int:
        """Forget every recorded request, returning how many there were. State is
        untouched: this is not :meth:`reset`, but a line drawn under setup."""
        return int(self._json(self.client.delete("/__unit/requests"))["cleared"])

    def assert_called(
        self,
        operation_id: str,
        *,
        times: int | None = None,
        at_least: int | None = None,
    ) -> list[dict[str, Any]]:
        """Assert an operation was called, and list what was called if not. With
        neither argument: at least once; ``times`` is exact and ``at_least`` a
        floor, and passing both is a ``ValueError``. Returns the records."""
        if times is not None and at_least is not None:
            raise ValueError("pass times= or at_least=, not both: one of the two is always redundant")
        capacity = self._request_capacity()
        if capacity == 0:
            # Refused rather than answered "saw 0" for a unit never recording.
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
        """The log's own bound, so a count is over everything it holds. Asked
        rather than assumed, because a profile may have raised or lowered it."""
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
        """Register a subscriber through the control plane, pre-verified and
        vendor-neutral. ``signature_key`` is the HMAC key for Square and the
        ``X-Clover-Auth`` code for Clover. ``event_types`` are checked against the
        vendor's vocabulary, a foreign type otherwise registering and never
        firing; a glob passes if it matches one published type."""
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
        """Wait for pending deliveries, retries included, to settle.
        ``POST /__unit/webhooks/drain`` is pass-bounded rather than "until
        settled", so this checks afterwards and raises ``RuntimeError`` naming the
        still-pending timer; for an uncompressed schedule use a virtual clock and
        :meth:`advance_clock`. ``timeout_s`` bounds the HTTP wait, and is not
        honoured against a :func:`unit` client."""
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
        """Delivery retries still scheduled, as the clock reports them; empty means
        settled. The filter mirrors the dispatcher's own timer labelling."""
        timers = self.info()["clock"]["pending_timers"]
        return [timer for timer in timers if str(timer.get("label", "")).startswith("webhook")]

    # -- state and faults ----------------------------------------------------

    def reset(self) -> dict[str, Any]:
        """Back to the seed scenario, and only the scenario -- what makes one unit
        shareable across tests. A session-scoped unit needs this in a per-test
        fixture with :meth:`reset_chaos` beside it, rules being what a reset leaves
        armed. The log and journal are cleared, a virtual clock is not rewound,
        and everything a test created goes, subscribers included."""
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
    """From :func:`unit`: the unit itself is reachable for anything the control
    plane does not cover. ``unit("square")`` yields ``StartedUnit[SquareSeed]``;
    written bare it is ``StartedUnit[Any]``."""

    unit: Unit = field(kw_only=True)
    #: The transport behind :attr:`Driver.client`, shared with
    #: :attr:`async_client`. Optional, so a hand-built ``StartedUnit`` still works.
    _transport: UnitTransport | None = field(default=None, kw_only=True, repr=False)

    @property
    def async_client(self) -> httpx.AsyncClient:
        """An ``httpx.AsyncClient`` onto the same unit, sharing :attr:`client`'s
        transport instance, so the two are interchangeable views of one unit."""
        if self._async_client is None:
            transport = self._transport if self._transport is not None else UnitTransport(self.unit)
            self._async_client = httpx.AsyncClient(
                transport=transport, base_url=self.base_url, timeout=CLIENT_TIMEOUT_S
            )
        return self._async_client


@dataclass
class ServedUnit(Driver[SeedT]):
    """From :func:`served`: a child process the block will stop."""

    process: subprocess.Popen[str] = field(kw_only=True)
    _output: _ChildOutput = field(kw_only=True, repr=False)

    @property
    def pid(self) -> int:
        return self.process.pid

    def logs(self) -> list[str]:
        """The child's most recent output, stdout and stderr interleaved, bounded
        by :data:`LOG_LINES`. The pipe must be read continuously or the child
        blocks on it; the reader thread keeps this tail as it goes."""
        return self._output.tail()


NO_SEED_HINT = (
    "vendorfake ships a seed for square, clover, toast and lightspeed. A vendor from the "
    "'vendorfake.vendors' entry-point group publishes its own by implementing "
    "vendorfake.core.kernel.types.SeedingVendor -- a seed(vendor_config) method "
    "returning an object with credentials, auth, read_only_auth and event_types. "
    "This one does not, so read its ids and tokens from that distribution's own "
    "constants instead, and drive the unit with create_unit()."
)
"""What a caller can do about a vendor with no seed. Split out so the message is
one string a test can assert on. It names the hook first, being the fix a
vendor's author makes once for everyone."""


def _require_seed(vendor: str, profile: str, found: Seed | None) -> Seed:
    """``found``, or a ``LookupError`` that says why there is none. A missing seed
    is a property of the vendor, so it is answered once when the unit starts. The
    per-vendor narrowing comes from ``unit()``'s overloads, not from here."""
    if found is None:
        raise LookupError(f"vendor {vendor!r} (profile {profile!r}) publishes no seed. {NO_SEED_HINT}")
    return found


def _refuse_a_seed_bound_overlay(
    vendor: str,
    profile: str,
    config: ResolvedConfig,
    definition: VendorDefinition | None,
) -> None:
    """Refuse an overlay that would make ``.seed`` describe a different unit: the
    seed is built from the vendor's constants, so overlaying the collection those
    are the values of would answer 401 to every request with nothing to say why.
    Checked against the overlay's own keys, however the overlay arrived."""
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
    vendor: Literal["lightspeed"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: LightspeedSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractContextManager[StartedUnit[LightspeedSeed]]: ...


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

    The overloads bind the seed type, a plain ``str`` vendor yielding
    ``StartedUnit[Seed]``; the implementation delegates to a private generator
    because ``@contextmanager`` and overloads do not compose in either checker.
    ``profile=None`` resolves in the three steps ``vendorfake serve`` uses: the
    argument, ``VENDORFAKE_PROFILE`` in this call's ``env=``, then ``full``, and
    passing both ``profile`` and ``capabilities`` is a ``ValueError``.
    ``seed_overlay`` is a partial seed document merged over the profile's, typed
    per vendor literal, whose ``tokens`` and identity collections are refused.
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
    """The body of :func:`unit`. See that function for the contract. ``env`` is the
    whole ``VENDORFAKE_*`` layer, empty by default, with ``seed``,
    ``seed_overlay`` and ``clock_start`` as layers under it."""
    # The one resolution order every binding shares: ambient VENDORFAKE_* variables,
    # then the keyword arguments' layer, then the caller's ``env`` mapping.
    environ: dict[str, str] = registry.ambient_env()
    if seed is not None:
        environ["VENDORFAKE_CHAOS_SEED"] = str(seed)
    if clock_start is not None:
        environ["VENDORFAKE_CLOCK_START"] = _clock_start_env_value(clock_start)
    if seed_overlay is not None:
        environ["VENDORFAKE_SEED_OVERLAY"] = _seed_overlay_env_value(seed_overlay)
    environ.update(env or {})
    built = create_unit(
        vendor=vendor,
        profile=profile,
        capabilities=capabilities,
        env=environ,
        sink=sink,
        logger=JsonLogger("warn") if logger is None else logger,
    )
    transport = UnitTransport(built, unmatched=unmatched)
    started: StartedUnit[Any] | None = None
    try:
        # Before the client, so a refusal leaves the unit stopped, and named with
        # the RESOLVED vendor. `definition=` is the exact `VendorDefinition` this
        # unit runs on: omitting it makes a second, freshly seeded one.
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
                _transport=transport,
            )
            yield started
    finally:
        if started is not None:
            _release_async_client(started)
        built.stop()


def _near_miss_message(response: httpx.Response) -> str:
    request = response.request
    lines = [f"vendorfake: no route matched {request.method} {request.url.path} on the served unit"]
    try:
        candidates = json.loads(response.headers[NEAR_MISS_HEADER])
    except (KeyError, ValueError):
        candidates = []
    if candidates:
        lines.append("Closest routes:")
        lines.extend(f"  {c.get('route')}  {c.get('operation_id') or ''}  {c.get('score')}" for c in candidates)
    lines.append('Pass unmatched="vendor-404" to get the 404 instead.')
    return "\n".join(lines)


def _raise_on_near_miss(response: httpx.Response) -> None:
    """The response hook behind ``unmatched="error"`` on an HTTP driver: the served
    unit answers 404 with the near-miss header, and the Python driver raises."""
    if NEAR_MISS_HEADER in response.headers:
        raise UnmatchedRequest(_near_miss_message(response))


async def _raise_on_near_miss_async(response: httpx.Response) -> None:
    _raise_on_near_miss(response)


def _http_client(base_url: str, unmatched: UnmatchedPolicy | None) -> httpx.Client:
    """The sync client of an HTTP driver, with the near-miss hook under ``"error"``."""
    policy = checked_unmatched(unmatched) or DEFAULT_INPROCESS_POLICY
    hooks: dict[str, list[Any]] = {"response": [_raise_on_near_miss]} if policy == "error" else {}
    return httpx.Client(base_url=base_url, timeout=CLIENT_TIMEOUT_S, event_hooks=hooks)


def _async_hooks(client: httpx.Client) -> dict[str, list[Any]]:
    """The async twin of ``client``'s response hooks."""
    return {"response": [_raise_on_near_miss_async]} if client.event_hooks.get("response") else {}


def _release_async_client(started: Driver[Any]) -> None:
    """Close a lazily built :attr:`Driver.async_client` from sync code. With no
    loop running ``asyncio.run`` closes it; with one running nothing is done."""
    client = started._async_client
    if client is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running: close on a fresh one. A client whose own loop has already
        # closed cannot be closed again and is left to the garbage collector.
        with contextlib.suppress(RuntimeError):
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
    vendor: Literal["lightspeed"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    sink: DeliverySink | None = ...,
    env: Mapping[str, str] | None = ...,
    logger: Logger | None = ...,
    seed: int | None = ...,
    seed_overlay: LightspeedSeedOverlay | str | os.PathLike[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    clock_start: datetime | str | None = ...,
) -> AbstractAsyncContextManager[StartedUnit[LightspeedSeed]]: ...


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
    """:func:`unit`, for a consumer whose fixtures are ``async def``. Yields the
    same :class:`StartedUnit` with the same arguments and overloads. What it adds
    is the exit: :meth:`StartedUnit.aclose` is awaited."""
    return _async_unit(
        vendor,
        profile,
        capabilities=capabilities,
        sink=sink,
        env=env,
        logger=logger,
        seed=seed,
        seed_overlay=seed_overlay,
        # Checked at the call, so the refusal lands on the line that spelled the
        # value rather than on ``__aenter__``.
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
    """A real server in front of ``started``'s unit, on a background thread. Yields
    a second :class:`Driver` onto the same unit, so state written through either
    client is visible through the other."""
    from vendorfake.asgi import create_app
    from vendorfake.asgi import serve_in_thread as serve_app

    policy = started._transport.unmatched if started._transport is not None else None
    with (
        serve_app(create_app(started.unit), host=host, port=port) as base_url,
        _http_client(base_url, policy) as client,
    ):
        driver = Driver(
            vendor=started.vendor, profile=started.profile, base_url=base_url, client=client, seed=started.seed
        )
        try:
            yield driver
        finally:
            _release_async_client(driver)


@overload
def served(
    vendor: Literal["square"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
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
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
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
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
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
    vendor: Literal["lightspeed"],
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
    port: int = ...,
    host: str = ...,
    log_level: str = ...,
    timeout_s: float = ...,
    env: Mapping[str, str] | None = ...,
    clock_start: datetime | str | None = ...,
    seed_overlay: LightspeedSeedOverlay | str | os.PathLike[str] | None = ...,
) -> AbstractContextManager[ServedUnit[LightspeedSeed]]: ...


@overload
def served(
    vendor: str,
    profile: str | None = ...,
    *,
    capabilities: Sequence[str] | None = ...,
    unmatched: UnmatchedPolicy | None = ...,
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
    profile: str | None = None,
    *,
    capabilities: Sequence[str] | None = None,
    unmatched: UnmatchedPolicy | None = None,
    port: int = 0,
    host: str = "127.0.0.1",
    log_level: str = "error",
    timeout_s: float = STARTUP_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
    clock_start: datetime | str | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
) -> AbstractContextManager[ServedUnit[Any]]:
    """``vendorfake serve`` in a child process, with its URL.

    Overloaded on the vendor literal the way :func:`unit` is. ``env`` is the
    ``VENDORFAKE_*`` layer for this one child on top of the environment it
    inherits, and ``clock_start`` layers the same way; :func:`_served` has the
    precedence and the names the mapping may not carry. ``seed_overlay`` reaches
    the child as ``VENDORFAKE_SEED_OVERLAY``. A session-scoped ``served()``
    against a vendor with rotating state needs :meth:`Driver.reset` between tests.
    """
    return _served(
        vendor,
        profile,
        capabilities=capabilities,
        unmatched=unmatched,
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
    profile: str | None = None,
    *,
    capabilities: Sequence[str] | None = None,
    unmatched: UnmatchedPolicy | None = None,
    port: int = 0,
    host: str = "127.0.0.1",
    log_level: str = "error",
    timeout_s: float = STARTUP_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
    clock_start: datetime | str | None = None,
    seed_overlay: SeedOverlay | str | os.PathLike[str] | None = None,
) -> Iterator[ServedUnit[Seed]]:
    """The body of :func:`served`. See that function for the contract.

    Runs the interpreter this test runs under, with ``port=0`` letting the system
    choose and ``SIGTERM`` stopping the child. ``env`` is layered onto this
    process's environment for the child, six names being refused before the spawn
    rather than silently beaten; both pipes are read on a daemon thread and
    ``timeout_s`` is a real deadline. Every ``VENDORFAKE_VENDOR_*`` entry the child
    will see is read here too, so the seed agrees with what it answers with."""
    # Resolved and refused before the child is spawned, so a seedless vendor or a
    # bad profile fails here rather than after a subprocess boots.
    #
    # `env=resolution_env` because the child inherits this process's `os.environ`
    # and `cli.py` layers every `VENDORFAKE_VENDOR_*` variable onto the profile's
    # `vendor` block; the selection is narrow -- that block and `VENDORFAKE_SEED`,
    # which decides the document the overlay is checked against -- so an unrelated
    # ambient variable cannot fail this call. `VENDORFAKE_PROFILE` is excluded
    # deliberately, the child being given `--profile` as a flag.
    #
    # A narrow, deliberate exception to `cli.py`'s "only module that reads
    # `os.environ`" invariant: `served()` hands a subprocess the real environment
    # by construction. `registry.resolve_vendor` is called as a module attribute
    # so a test that substitutes it reaches it here.
    definition = registry.resolve_vendor(vendor)
    resolved_name = definition.name
    # The child's layer, in the order the child will see it: `clock_start` first,
    # then the caller's mapping, so an explicit entry in `env` wins the kwarg.
    layer: dict[str, str] = {}
    if clock_start is not None:
        layer["VENDORFAKE_CLOCK_START"] = _clock_start_env_value(clock_start)
    layer.update(env or {})
    # Refused before the child exists rather than met as a 401 later: `.seed`
    # derives from the vendor's constants and never reads the document
    # `VENDORFAKE_SEED` names, so the child would hydrate another scenario.
    if "VENDORFAKE_SEED" in layer:
        raise ValueError(
            "served(env=...) cannot carry VENDORFAKE_SEED: the .seed handed back is derived from the vendor's "
            "constants, not from a seed document, and would not describe the child. Use a profile whose "
            "document points at the seed you want, and pass it as profile=."
        )
    # The same refusal one variable along: an `env` entry would reach the child
    # unvalidated, surfacing as a child that exited before announcing a port
    # rather than as the `UnitError` the parameter's own path raises.
    if "VENDORFAKE_SEED_OVERLAY" in layer:
        raise ValueError(
            "served(env=...) cannot carry VENDORFAKE_SEED_OVERLAY: pass seed_overlay= instead. The parameter "
            "takes the document itself as a mapping (or a path), encodes it for the child, and refuses an "
            "unknown collection where the caller can see it -- an env entry would reach the child unchecked "
            "and surface as a child that exited before announcing a port."
        )
    # The same refusal for the variables an explicit flag below would silently
    # beat: an entry that changes nothing is worse than one that is refused.
    beaten = sorted(_FLAG_BEATEN_ENV & set(layer))
    if beaten:
        raise ValueError(
            f"served(env=...) cannot carry {', '.join(beaten)}: served() passes {_FLAG_BEATEN_HINT} to the child "
            "as explicit flags, and the CLI prefers a flag to the variable, so the entry would change nothing. "
            "Use the parameter instead."
        )
    resolved_profile, capability_layer = registry.resolve_capabilities(definition, profile, capabilities)
    layer.update(capability_layer)
    child_view = {**os.environ, **layer}
    # The same resolution order as unit() and the CLI: the argument, else the
    # variable the child will see, else the default. Passed to the child as a flag
    # so parent and child agree on the seed.
    profile = resolved_profile or child_view.get("VENDORFAKE_PROFILE") or DEFAULT_PROFILE_NAME
    resolution_env = {
        key: value for key, value in child_view.items() if key.startswith(ENV_VENDOR_PREFIX) or key == ENV_SEED
    }
    if seed_overlay is not None:
        # Encoded once and used twice: the child reads it from its environment,
        # and the `load_profile` below reads it here so a bad collection raises
        # `UnitError` before `Popen` rather than killing a silent child.
        overlay_value = _seed_overlay_env_value(seed_overlay)
        resolution_env["VENDORFAKE_SEED_OVERLAY"] = overlay_value
        layer["VENDORFAKE_SEED_OVERLAY"] = overlay_value
    loaded = load_profile(
        profile_dir=definition.profile_dir,
        name=profile,
        base_dir=definition.base_dir,
        env=resolution_env,
        defaults=definition.retry_defaults,
    )
    # Before `Popen`, like every other refusal here: the child would hydrate the
    # overlaid credentials while `resolved_seed` still carried the shipped ones.
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
    # The second documented exception to `cli.py`'s `os.environ` invariant:
    # `Popen`'s `env=` replaces rather than layers, so adding one variable to the
    # environment it would otherwise inherit has no path that avoids this read.
    child_env = None if not layer else {**os.environ, **layer}
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env)
    output = _ChildOutput(process)
    try:
        base_url = _wait_for_announcement(process, output, timeout_s)
        with _http_client(base_url, unmatched) as client:
            health = client.get("/__unit/health").json()
            yield ServedUnit(
                vendor=str(health["vendor"]),
                profile=str(health["profile"]),
                base_url=base_url,
                client=client,
                # Built above from the same profile document and environment
                # layer the child resolves its own config from, not read back
                # over the wire, which no route publishes.
                seed=resolved_seed,
                process=process,
                _output=output,
            )
    finally:
        _stop(process)
        output.join()


_FLAG_BEATEN_ENV: frozenset[str] = frozenset({"VENDORFAKE_HOST", "VENDORFAKE_PORT", "VENDORFAKE_LOG_LEVEL"})
"""The ``VENDORFAKE_*`` names an ``env=`` entry to :func:`served` is refused for,
the child getting each as a flag that beats the variable."""

_FLAG_BEATEN_HINT = "host=, port= and log_level="

SERVE_COMMAND: tuple[str, ...] = (sys.executable, "-m", "vendorfake", "serve")
"""What :func:`served` runs, before the flags. A module attribute so a test of
the startup deadline can substitute a child that never announces."""

LOG_LINES = 500
"""How much of a child's output :meth:`ServedUnit.logs` keeps."""


class _ChildOutput:
    """Reads a child's stdout and stderr to the end, on threads: a bounded tail for
    :meth:`ServedUnit.logs`, and a queue so the announcement can be waited for."""

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
